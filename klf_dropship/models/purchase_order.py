from odoo import models, api


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        """
        Auto-populate x_studio_po_no from the purchase order origin field.
        Links the PO line back to the original Sales Order.
        """
        lines = super().create(vals_list)
        for line in lines:
            if line.order_id and line.order_id.origin:
                # Find the sale order matching the origin
                sale_order = self.env['sale.order'].search([
                    ('name', '=', line.order_id.origin)
                ], limit=1)
                if sale_order:
                    line.x_studio_po_no = sale_order.id
        return lines
