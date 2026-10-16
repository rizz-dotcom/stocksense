import pytest

from stocksense.exceptions import ImportValidationError
from stocksense.services import import_service, inventory_service, sales_service
from stocksense.utils.csv_utils import dataframe_to_safe_csv, neutralize_cell

INV_HEADER = "sku,name,category,unit,stock_qty,safety_stock,reorder_threshold,expiry_date\n"
MAX = 100_000


def preview(db, user, kind, text):
    return import_service.preview_import(db, user, kind, text.encode("utf-8"), max_bytes=MAX)


def commit(db, user, kind, text):
    return import_service.commit_import(db, user, kind, text.encode("utf-8"), max_bytes=MAX)


def test_valid_inventory_import(db, staff):
    text = INV_HEADER + "A,Buns,Bakery,pcs,10,5,8,2026-12-31\nB,Sauce,Cond,pcs,\"1,200\",100,0,\n"
    p = preview(db, staff, "inventory", text)
    assert p.can_commit and p.new_count == 2
    assert commit(db, staff, "inventory", text) == {"new": 2, "updated": 0, "unchanged": 0}
    assert {x["sku"]: x["stock_qty"] for x in inventory_service.list_products(db)} == {"A": 10, "B": 1200}


def test_repeated_import_is_idempotent(db, staff):
    text = INV_HEADER + "A,Buns,Bakery,pcs,10,5,8,\n"
    commit(db, staff, "inventory", text)
    assert commit(db, staff, "inventory", text) == {"new": 0, "updated": 0, "unchanged": 1}
    assert len(inventory_service.list_products(db)) == 1


@pytest.mark.parametrize("text", ["", "   \n", INV_HEADER])
def test_empty_files(db, staff, text):
    with pytest.raises(ImportValidationError):
        preview(db, staff, "inventory", text)


@pytest.mark.parametrize("header", ["sku,name\n", INV_HEADER.replace("unit", "units"),
                                    INV_HEADER.strip() + ",extra\n"])
def test_wrong_headers(db, staff, header):
    with pytest.raises(ImportValidationError):
        preview(db, staff, "inventory", header + "A,B,C,D,1,1,1,\n")


def test_malformed_csv(db, staff):
    with pytest.raises(ImportValidationError):
        preview(db, staff, "inventory", INV_HEADER + 'A,"unclosed,x,pcs,1,1,1,\n')


def test_binary_and_oversized(db, staff):
    with pytest.raises(ImportValidationError):
        import_service.preview_import(db, staff, "inventory", b"\x00\x01\x02", max_bytes=MAX)
    with pytest.raises(ImportValidationError):
        import_service.preview_import(db, staff, "inventory", b"a" * 200, max_bytes=100)


@pytest.mark.parametrize("row,needle", [
    ("A,Buns,B,pcs,-5,1,1,", "negative"),
    ("A,Buns,B,pcs,1.5,1,1,", "whole number"),
    ("A,Buns,B,pcs,NaN,1,1,", "not a valid number"),
    ("A,Buns,B,pcs,inf,1,1,", "not a valid number"),
    ("A,Buns,B,pcs,12,5,1,", None),
    ("A,=cmd|' /C calc'!A0,B,pcs,1,1,1,", "formula"),
    ("A,Buns,B,pcs,1,1,1,05/10/2026", "supported format"),
    ("A,Buns,B,pcs,1,1,1,2026-02-30", "real calendar date"),
    ("A,Buns,B,pcs,\"12,5\",1,1,", "comma"),
])
def test_row_validation(db, staff, row, needle):
    p = preview(db, staff, "inventory", INV_HEADER + row + "\n")
    if needle is None:
        assert p.can_commit
    else:
        assert p.rejected and needle in p.rejected[0]["error"]


def test_duplicates_within_file(db, staff):
    p = preview(db, staff, "inventory", INV_HEADER + "A,X,B,pcs,1,1,1,\nA,Y,B,pcs,2,1,1,\n")
    assert len(p.rejected) == 1 and "Duplicate" in p.rejected[0]["error"]


def test_invalid_rows_do_not_corrupt_existing_data(db, staff):
    commit(db, staff, "inventory", INV_HEADER + "A,Buns,B,pcs,10,1,1,\n")
    bad = INV_HEADER + "A,Buns,B,pcs,99,1,1,\nC,Bad,B,pcs,-1,1,1,\n"
    with pytest.raises(ImportValidationError):
        commit(db, staff, "inventory", bad)
    products = inventory_service.list_products(db)
    assert len(products) == 1 and products[0]["stock_qty"] == 10


def test_sales_unknown_product_and_correction(db, staff, factory):
    factory.product("BUN")
    header = "sale_date,sku,quantity\n"
    p = preview(db, staff, "sales", header + "2026-10-01,GHOST,5\n")
    assert "Unknown product" in p.rejected[0]["error"]
    commit(db, staff, "sales", header + "2026-10-01,BUN,5\n")
    p2 = preview(db, staff, "sales", header + "2026-10-01,BUN,7\n")
    assert p2.update_count == 1
    commit(db, staff, "sales", header + "2026-10-01,BUN,7\n")
    assert sales_service.list_sales(db)[0]["quantity"] == 7


def test_supplier_import_and_conflicts(db, staff, factory):
    factory.product("BUN")
    header = ("supplier_code,supplier_name,contact_name,phone,email,sku,unit_price,"
              "min_order_qty,available_qty,lead_time_days,quoted_at\n")
    ok = header + "S1,Supp,Ann,0300-1,a@b.example,BUN,12.50,10,100,2,2026-10-15\n"
    assert commit(db, staff, "suppliers", ok)["new"] == 1
    conflict = header + ("S2,One,,,,BUN,1,1,1,1,2026-10-15\n"
                         "S2,Different name,,,,BUN,1,1,1,1,2026-10-15\n")
    assert preview(db, staff, "suppliers", conflict).rejected
    bad_fk = header + "S3,X,,,,NOPE,1,1,1,1,2026-10-15\n"
    assert "Unknown product" in preview(db, staff, "suppliers", bad_fk).rejected[0]["error"]
    bad_price = header + "S4,X,,,,BUN,0,1,1,1,2026-10-15\n"
    assert preview(db, staff, "suppliers", bad_price).rejected


def test_failed_validation_is_audited(db, staff):
    from stocksense.services import audit_service
    preview(db, staff, "inventory", INV_HEADER + "A,Buns,B,pcs,-5,1,1,\n")
    assert audit_service.list_events(db, actions=["CSV_IMPORT_VALIDATION_FAILED"])


def test_export_neutralizes_formula_injection():
    import pandas as pd
    for value in ("=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tx"):
        assert neutralize_cell(value).startswith("'")
    assert neutralize_cell("Buns") == "Buns" and neutralize_cell(5) == 5
    out = dataframe_to_safe_csv(pd.DataFrame({"name": ["=HYPERLINK(\"x\")", "ok"]})).decode("utf-8-sig")
    assert "'=HYPERLINK" in out and "\n=HYPERLINK" not in out
