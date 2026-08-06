frappe.listview_settings['Intercompany Ledger'] = {
	add_fields: ['entry_type', 'status', 'amount', 'currency', 'source_company', 'target_company'],
	get_indicator(doc) {
		const map = {
			Pending: ['Pending', 'orange'],
			Accepted: ['Accepted', 'green'],
			Rejected: ['Rejected', 'red'],
			Failed: ['Failed', 'red'],
			Success: ['Success', 'green'],
			Queued: ['Queued', 'blue'],
		};
		const v = map[doc.status] || ['', 'grey'];
		return [__(v[0]), v[1], `status,=,${doc.status}`];
	},
	onload(listview) {
		listview.page.add_actions_menu_item(__('Bulk accept'), () => {
			const names = listview
				.get_checked_items()
				.filter((d) => d.entry_type === 'Transaction')
				.map((d) => d.name);
			if (!names.length) {
				frappe.msgprint(__('Select at least one Transaction row'));
				return;
			}
			frappe.call({
				method: 'intercompany.intercompany.doctype.intercompany_ledger.intercompany_ledger.bulk_accept',
				args: { names: JSON.stringify(names) },
				callback: (r) => {
					frappe.msgprint(__('Processed {0} rows', [names.length]));
					listview.refresh();
				},
			});
		});
	},
};
