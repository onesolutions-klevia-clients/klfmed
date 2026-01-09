from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'
    # x_studio_supplier_po is populated automatically when a PO is created
    # See purchase_order.py -> PurchaseOrder.create()

