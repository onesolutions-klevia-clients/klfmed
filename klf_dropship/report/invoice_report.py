import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class KlfmedInvoiceReport(models.AbstractModel):
    _name = 'report.klf_dropship.report_invoice_klfmed'
    _description = 'KLFMed Commercial Invoice Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        """Populate logistics fields on invoice lines before rendering the report."""
        _logger.info('KLFMed Invoice Report: _get_report_values called for docids=%s', docids)
        docs = self.env['account.move'].browse(docids)

        for move in docs:
            # Re-populate header-level fields from pickings
            move._populate_from_picking()

            # Re-populate line-level fields
            for line in move.invoice_line_ids:
                line._populate_lot_number()
                line._populate_expiration_date()
                line._populate_po_no()
                line._populate_delivery_date()

        # Pre-compute unique PO numbers per invoice for the template
        po_numbers_map = {}
        for move in docs:
            _logger.info('PO_MAP DEBUG move %s: invoice_line_ids count=%s', move.id, len(move.invoice_line_ids))
            for line in move.invoice_line_ids:
                _logger.info('PO_MAP DEBUG line %s: display_type=%s, x_studio_po_no_ref=%s',
                             line.id, line.display_type, line.x_studio_po_no_ref)
            po_numbers = list(dict.fromkeys(
                line.x_studio_po_no_ref
                for line in move.invoice_line_ids
                if line.display_type not in ('line_section', 'line_note') and line.x_studio_po_no_ref
            ))
            po_numbers_map[move.id] = ', '.join(po_numbers)
            _logger.info('PO_MAP DEBUG result for move %s: %s', move.id, po_numbers_map[move.id])

        return {
            'doc_ids': docids,
            'doc_model': 'account.move',
            'docs': docs,
            'po_numbers_map': po_numbers_map,
        }
