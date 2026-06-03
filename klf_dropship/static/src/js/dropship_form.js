/** @odoo-module **/
import { patch } from '@web/core/utils/patch';
import { FormController } from '@web/views/form/form_controller';
import { onMounted } from '@odoo/owl';

patch(FormController.prototype, {
	setup() {
		super.setup(...arguments);
		onMounted(() => {
			console.log('Hello World', this.props);
			if (this.props.resModel === 'stock.picking') {
				const record = this.model.root;
				const pickingTypeName = record.data.picking_type_id?.[1] || '';
				if (pickingTypeName.toLowerCase().includes('dropship')) {
					console.log('Hello Dropship');
				}
			}
		});
	},
});
