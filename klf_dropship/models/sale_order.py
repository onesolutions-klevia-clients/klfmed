import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.onchange('partner_id')
    def _onchange_partner_id_set_defaults(self):
        """
        Auto-fill default incoterm from customer record when partner changes.
        Sources from x_studio_default_incoterm on res.partner.
        """
        for order in self:
            if order.partner_id and order.partner_id.x_studio_default_incoterm:
                order.incoterm = order.partner_id.x_studio_default_incoterm
                _logger.warning("KLF_DROPSHIP: SO %s set incoterm from customer %s",
                             order.name, order.partner_id.name)
