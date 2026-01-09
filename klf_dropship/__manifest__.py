{
    'name': 'KLFMed Dropship Customization',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Dropshipping',
    'summary': 'Custom dropshipment workflow automation for KLFMed Switzerland',
    'description': """
KLFMed Custom Dropshipment Module
=================================

This module implements the automation logic for KLFMed's dropshipping workflow.

Note: All custom fields (x_studio_*) are created via Odoo Studio.
This module only handles the automatic population of those fields.

Automations:
------------
* Sale Order: Auto-links x_studio_supplier_po at confirmation
* Purchase Order Line: Auto-populates x_studio_po_no from SO origin
* Stock Move: Auto-populates x_studio_po_no from PO origin
* Account Move: Auto-populates logistics fields from related pickings
* Account Move Line: Auto-populates x_studio_po_no from related SO/PO
    """,
    'author': 'One Solutions',
    'website': 'https://onesolutions.io',
    'license': 'LGPL-3',
    'depends': [
        'sale',
        'purchase',
        'stock',
        'account',
    ],
    'data': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}