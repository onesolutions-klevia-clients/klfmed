from odoo import api, models


class KlfmedInvoiceReport(models.AbstractModel):
    _name = 'report.klf_dropship.report_invoice_klfmed'
    _description = 'KLFMed Commercial Invoice Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        """Populate logistics fields on invoice lines before rendering the report."""
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

        return {
            'doc_ids': docids,
            'doc_model': 'account.move',
            'docs': docs,
        }
