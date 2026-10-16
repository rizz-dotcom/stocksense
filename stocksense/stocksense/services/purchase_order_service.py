"""SIMULATED purchase orders.

Nothing here contacts a supplier, sends an order or spends money. Every row is
created with demo_only=True and real_supplier_order_placed=False, and database
CHECK constraints make any other value impossible.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from stocksense.exceptions import InvalidTransitionError, NotFoundError
from stocksense.models import (Product, Proposal, ProposalVersion, PurchaseOrder,
                               PurchaseOrderItem, Supplier)
from stocksense.security.authorization import Permission, UserContext, require
from stocksense.services import audit_service
from stocksense.utils.dates import utcnow
from stocksense.utils.money import format_money


def create_orders_for_approval(session, proposal: Proposal, version: ProposalVersion,
                               actor: UserContext) -> list[int]:
    grouped: dict[int, list] = defaultdict(list)
    for item in version.items:
        if item.funded and item.quantity > 0 and item.supplier_id:
            grouped[item.supplier_id].append(item)
    order_ids = []
    for supplier_id, items in sorted(grouped.items()):
        supplier = session.get(Supplier, supplier_id)
        total = sum(i.line_total_minor for i in items)
        po = PurchaseOrder(
            po_number=f"SIM-PO-{proposal.id:05d}-{supplier.code}"[:40],
            proposal_id=proposal.id, proposal_version=version.version, supplier_id=supplier_id,
            status="SIMULATED_OPEN", total_minor=total, currency=proposal.currency,
            demo_only=True, real_supplier_order_placed=False, created_by=actor.username)
        session.add(po)
        session.flush()
        for i in items:
            session.add(PurchaseOrderItem(order_id=po.id, product_id=i.product_id,
                                          quantity=i.quantity, unit_price_minor=i.unit_price_minor,
                                          line_total_minor=i.line_total_minor))
        audit_service.record(
            session, actor=actor.username, action="SIMULATED_PO_CREATED",
            entity_type="purchase_order", entity_id=str(po.id), new_state="SIMULATED_OPEN",
            message=f"{po.po_number} created for {supplier.name}, "
                    f"{format_money(total, proposal.currency)}. SIMULATED - no real order placed.")
        order_ids.append(po.id)
    session.flush()
    return order_ids


def _order_dict(po: PurchaseOrder, supplier: Supplier, items=None) -> dict:
    d = {"id": po.id, "po_number": po.po_number, "proposal_id": po.proposal_id,
         "proposal_version": po.proposal_version, "supplier": supplier.name,
         "supplier_code": supplier.code, "status": po.status, "total_minor": po.total_minor,
         "currency": po.currency, "demo_only": po.demo_only,
         "real_supplier_order_placed": po.real_supplier_order_placed,
         "created_by": po.created_by, "created_at": po.created_at, "received_at": po.received_at}
    if items is not None:
        d["items"] = items
    return d


def list_orders(db, proposal_id: int | None = None) -> list[dict]:
    with db.session() as s:
        q = select(PurchaseOrder, Supplier).join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        if proposal_id:
            q = q.where(PurchaseOrder.proposal_id == proposal_id)
        return [_order_dict(po, sup) for po, sup in
                s.execute(q.order_by(PurchaseOrder.id.desc())).all()]


def get_order(db, order_id: int) -> dict:
    with db.session() as s:
        po = s.get(PurchaseOrder, order_id)
        if po is None:
            raise NotFoundError("Purchase order not found.")
        sup = s.get(Supplier, po.supplier_id)
        items = []
        for it, prod in s.execute(select(PurchaseOrderItem, Product)
                                  .join(Product, Product.id == PurchaseOrderItem.product_id)
                                  .where(PurchaseOrderItem.order_id == po.id)).all():
            items.append({"sku": prod.sku, "product": prod.name, "unit": prod.unit,
                          "quantity": it.quantity, "unit_price_minor": it.unit_price_minor,
                          "line_total_minor": it.line_total_minor})
        return _order_dict(po, sup, items)


def mark_received(db, actor: UserContext, order_id: int) -> None:
    """Record that a (simulated) delivery arrived: adds quantities to STOCKSENSE
    on-hand stock only. No external system is changed."""
    require(actor, Permission.RECEIVE_ORDERS)
    with db.session() as s:
        po = s.get(PurchaseOrder, order_id)
        if po is None:
            raise NotFoundError("Purchase order not found.")
        if po.status != "SIMULATED_OPEN":
            raise InvalidTransitionError(f"{po.po_number} is already {po.status}.")
        for item in s.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.order_id == po.id)):
            product = s.get(Product, item.product_id)
            product.stock_qty += item.quantity
        po.status = "SIMULATED_RECEIVED"
        po.received_at = utcnow()
        audit_service.record(s, actor=actor.username, action="SIMULATED_PO_RECEIVED",
                             entity_type="purchase_order", entity_id=str(po.id),
                             previous_state="SIMULATED_OPEN", new_state="SIMULATED_RECEIVED",
                             message=f"{po.po_number} marked received; STOCKSENSE stock "
                                     "increased. No external inventory was changed.")
