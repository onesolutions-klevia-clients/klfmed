from odoo import models, api


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    @api.model_create_multi
    def create(self, vals_list):
        """
        Auto-populate x_studio_supplier_po on the related Sales Order.
        When a PO is created from a SO (dropship), link it back to the SO.
        """
        orders = super().create(vals_list)
        for order in orders:
            if order.origin:
                # Find the sale order matching the origin
                sale_order = self.env['sale.order'].search([
                    ('name', '=', order.origin)
                ], limit=1)
                if sale_order:
                    sale_order.x_studio_supplier_po = order.id
                    # Copy customer PO number from SO to PO header
                    if not order.x_studio_customer_po_no and sale_order.x_studio_purchase_order_number:
                        order.x_studio_customer_po_no = sale_order.x_studio_purchase_order_number
                    # Also update PO lines with the customer PO number from SO header
                    po_number = sale_order.x_studio_purchase_order_number
                    for line in order.order_line:
                        if not line.x_studio_po_no and po_number:
                            line.x_studio_po_no = po_number
        return orders


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        """
        Auto-populate fields from the related Sales Order for lines
        created after the PO (e.g. lines added manually later).
        The initial lines are already handled by PurchaseOrder.create.
        """
        lines = super().create(vals_list)
        for line in lines:
            if not line.x_studio_po_no and line.order_id and line.order_id.origin:
                sale_order = self.env['sale.order'].search([
                    ('name', '=', line.order_id.origin)
                ], limit=1)
                if sale_order and sale_order.x_studio_purchase_order_number:
                    line.x_studio_po_no = sale_order.x_studio_purchase_order_number
            # Propagate delivery date from SO line to PO line
            if not line.x_studio_delivery_date and line.sale_line_id and line.sale_line_id.x_studio_delivery_date:
                line.x_studio_delivery_date = line.sale_line_id.x_studio_delivery_date
        return lines
