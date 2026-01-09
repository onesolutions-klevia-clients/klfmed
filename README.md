# KLFMed Odoo Modules

Custom Odoo modules for KLFMed Switzerland.

## Modules

### klf_dropship - Custom Dropshipment Automation

Module implementing automation logic for KLFMed's dropshipping workflow.

> **Note**: All custom fields (`x_studio_*`) are created via Odoo Studio.  
> This module only handles the automatic population of those fields.

#### Automations

| Model | Field | Trigger | Logic |
|-------|-------|---------|-------|
| Sale Order | `x_studio_supplier_po` | `action_confirm` | Links to generated PO |
| Purchase Order Line | `x_studio_po_no` | `create` | Populates from PO origin → SO |
| Stock Move | `x_studio_po_no` | `create` | Populates from PO origin → SO |
| Account Move | logistics fields | `create` | Populates from related picking |
| Account Move Line | `x_studio_po_no` | `create` | Populates from related SO/PO |

#### Process Flow

```
1. SO Confirmation  → x_studio_supplier_po = linked PO
2. PO Line Creation → x_studio_po_no = origin SO
3. Stock Move       → x_studio_po_no = origin SO
4. Invoice Creation → logistics fields from picking
                    → x_studio_po_no on lines
```

#### Dependencies

- `sale`
- `purchase`
- `stock`
- `account`

#### Installation

```bash
# Via command line
odoo -c /etc/odoo.conf -i klf_dropship -d <database_name> --stop-after-init

# Or update if already installed
odoo -c /etc/odoo.conf -u klf_dropship -d <database_name> --stop-after-init
```

#### Version

- Odoo 19.0
- Module version: 19.0.1.0.0
