from odoo import models, api


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        """
        Override confirmation to link the generated PO to the SO.
        Populates x_studio_supplier_po field automatically.
        """
        res = super().action_confirm()
        for order in self:
            # Find the purchase order created from this sale order
            purchase_orders = self.env['purchase.order'].search([
                ('origin', '=', order.name)
            ], limit=1)
            if purchase_orders:
                order.x_studio_supplier_po = purchase_orders.id
        return res
