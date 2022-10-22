# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools import parse_date
from odoo.tools.float_utils import float_round

class AssetsReport(models.AbstractModel):
    _inherit = 'account.assets.report'

    def _get_reports_buttons(self):
        res = super()._get_reports_buttons()
        if self._is_lu_electronic_report():
            for re in res:
                if re.get('action') == 'print_xml':
                    # deactivate xml export & saving
                    # and allow export of the XML declaration from the wizard
                    re['name'] = _('EXPORT ECDF DECLARATION')
                    re['action'] = 'l10n_lu_open_report_export_wizard'
                    del re['file_export_type']
        return res

    def _is_lu_electronic_report(self):
        if self.env.company.country_id.code == 'LU':
            return True
        return super()._is_lu_electronic_report()

    def _get_lu_xml_2_0_report_values(self, options):
        """
        Returns the formatted values for the LU eCDF declaration "Tables of acquisitions / amortisable expenditures".
        (https://ecdf-developer.b2g.etat.lu/ecdf/forms/popup/AN_TABACAM_TYPE/2020/en/1/preview)
        """
        def _get_assets_data(lines):
            """
            Retrieves additional data (VAT paid, depreciable values) needed for the eCDF declaration
            "Tables of acquisitions / amortisable expenditures", that is not present in the result from _get_lines.
            The information about the VAT paid for the asset is retrieved from the tax's account.move.line
            of the account.move, taken in proportion to the price of the asset.

            :param lines: the result from _get_lines (the various assets)
            :return a dictionary containing:
                * 'tax_amounts': The VAT paid for each asset
                * 'depreciable_values': The depreciable value of each asset
            """
            # ids of asset lines are in the form <account_group>_<asset_id>
            asset_ids = {int(line['id'].split('_')[1]): line['id'] for line in lines if '_' in line['id']}
            assets = self.env['account.asset'].search([('id', 'in', list(asset_ids.keys()))])
            # Check that all assets are in EUR and that the company has EUR as its currency;
            asset_currencies = assets.mapped('currency_id')
            if any([curr != self.env.ref('base.EUR') for curr in list(asset_currencies) + [self.env.company.currency_id]]):
                raise ValidationError(_("Only assets having EUR currency for companies using EUR currency can be reported."))
            depreciable_values = {
                asset_ids.get(asset_id): float(original_value) - float(salvage_value)
                for (asset_id, salvage_value, original_value) in assets.mapped(
                    lambda r: (r.id, r.salvage_value, r.original_value)
                )
            }
            # The tax paid for the products in the asset must be retrieved from the original_move_line_ids
            tax_amounts = {}
            for asset in assets:
                total_tax = 0.00
                for ml in asset.original_move_line_ids:
                    balance = ml.balance
                    if asset.account_asset_id.multiple_assets_per_line and len(asset.original_move_line_ids) == 1:
                        balance /= max(1, int(ml.quantity))
                    for tax in ml.tax_ids:
                        # Take the corresponding tax account.move.line on the account.move if the tax is VAT and debit
                        tax_line = ml.move_id.line_ids.filtered(lambda r: r.tax_line_id == tax and r.tax_repartition_line_id and r.debit)
                        if tax_line:
                            total_tax += tax_line.balance * balance / tax_line.tax_base_amount
                if total_tax:  # only keep taxes with amounts different from 0
                    tax_amounts[asset_ids.get(asset['id'])] = total_tax
            return depreciable_values, tax_amounts

        lu_template_values = self._get_lu_electronic_report_values(options)

        date_from = fields.Date.from_string(options['date'].get('date_from'))
        date_to = fields.Date.from_string(options['date'].get('date_to'))
        values = {
            '233': {'field_type': 'number', 'value': date_from.day},
            '234': {'field_type': 'number', 'value': date_from.month},
            '235': {'field_type': 'number', 'value': date_to.day},
            '236': {'field_type': 'number', 'value': date_to.month}
        }

        lines = self.with_context(self._set_context(options))._get_lines(options)
        # get additional data not shown in the assets report (tax paid, depreciable value)
        depreciable_values, tax_amounts = _get_assets_data(lines)
        # format the values for XML report
        update_values, expenditures_table, depreciations_table = self._l10n_lu_get_expenditures_and_depreciations_tables(
            lines, tax_amounts, depreciable_values
        )
        values.update(update_values)
        # only add the tables if they contain data
        tables = [table for table in (expenditures_table, depreciations_table) if table]

        lu_template_values.update({
            'forms': [{
                'declaration_type': 'AN_TABACAM',
                'year': date_from.year,
                'period': '1',
                'currency': self.env.company.currency_id.name,
                'model': '1',
                'field_values': values,
                'tables': tables
            }]
        })
        return lu_template_values

    def _l10n_lu_get_expenditures_and_depreciations_tables(self, lines, tax_amounts, depreciable_values):
        """
        Returns the table to fill in the LU declaration "Tables of acquisitions / amortisable expenditures".

        :param lines: the lines from account.asset.report's _get_lines
        :param tax_amounts: dict containing the total tax paid on each asset
        :param depreciable_values: dict containing the depreciable amounts for each asset
        :return the formatted "Table of acquisitions", "Table of amortisable expenditures", and the table totals
        """
        update_values = {}
        expenditures_table = []
        depreciations_table = []

        N_expenditure = 0
        for line in lines:
            if line['level'] != 1:  # only 2 levels are possible, level 0 are totals
                continue
            # Update expenditures table
            N_expenditure += 1
            acquisition_date = parse_date(self.env, line['columns'][0]['name']).strftime("%d/%m/%Y")
            name = line['name']
            acquisition_cost_no_vat = float(line['columns'][4]['no_format_name']) + float(line['columns'][5]['no_format_name'])
            vat = tax_amounts.get(line['id'])
            value_to_be_depreciated = depreciable_values.get(line['id'])
            expenditures_line = {
                '501': {'field_type': 'number', 'value': str(N_expenditure)},
                '502': {'field_type': 'char', 'value': acquisition_date},
                '503': {'field_type': 'char', 'value': name}
            }
            if vat:
                expenditures_line.update({
                    '504': {'field_type': 'float', 'value': float_round(acquisition_cost_no_vat + vat, 2)},
                    '505': {'field_type': 'float', 'value': float_round(vat, 2)},
                })
            expenditures_line['506'] = {'field_type': 'float', 'value': float_round(acquisition_cost_no_vat, 2)}
            expenditures_line['508'] = {'field_type': 'float', 'value': float_round(value_to_be_depreciated, 2)}
            expenditures_table.append(expenditures_line)

            # Update Depreciation/amortisation table
            depreciation_or_amortisation = float(line['columns'][3]['name'][:-2])  # remove ' %' sign
            # Book value at the beginning of the reported accounting period (not reported by super's _get_lines)
            # asset_opening (acquisition price at the beginning of the accounting period)
            #  - depreciation_opening (depreciated value at the beginning of the accounting period)
            book_value_beginning = float(line['columns'][4]['no_format_name']) - float(line['columns'][8]['no_format_name'])
            acquisitions = float(line['columns'][5]['no_format_name'])
            sales = float(line['columns'][6]['no_format_name'])
            # Depreciation reported from _get_lines divided in value decrease (+) and value increase (-);
            # depreciation is the net difference
            depreciation = float(line['columns'][9]['no_format_name']) - float(line['columns'][10]['no_format_name'])
            book_value_end = float(line['columns'][12]['no_format_name'])
            depreciations_line = {
                '617': {'field_type': 'number', 'value': str(N_expenditure)},
                '602': {'field_type': 'char', 'value': acquisition_date},
                '601': {'field_type': 'char', 'value': name},
                '603': {'field_type': 'float', 'value': float_round(value_to_be_depreciated, 2)},
                '604': {'field_type': 'float', 'value': float_round(depreciation_or_amortisation, 2)},
                '605': {'field_type': 'float', 'value': float_round(book_value_beginning, 2)},
                '606': {'field_type': 'float', 'value': float_round(acquisitions, 2)},
                '607': {'field_type': 'float', 'value': float_round(sales, 2)},
                '608': {'field_type': 'float', 'value': float_round(depreciation, 2)},
                '609': {'field_type': 'float', 'value': float_round(book_value_end, 2)}
            }
            # the following fields are not required in the report; only report if they are different from 0
            for key in range(604, 610):
                if depreciations_line.get(str(key)) and depreciations_line[str(key)]['value'] == 0.00:
                    depreciations_line.pop(str(key))
            depreciations_table.append(depreciations_line)

        # Expenditures table totals
        total_vat = sum([i.get('505', {'value': 0.00})['value'] for i in expenditures_table])
        total_acquisition_cost = sum([i['506']['value'] for i in expenditures_table])
        totals_expenditures_table = {
            '509': {'field_type': 'float', 'value': total_vat},
            '510': {'field_type': 'float', 'value': total_acquisition_cost}
        }
        # Depreciations table totals
        total_book_value_beginning = sum([i.get('605') and i['605']['value'] or 0.0 for i in depreciations_table])
        total_acquisitions = sum([i.get('606') and i['605']['value'] or 0.0 for i in depreciations_table])
        total_sales = sum([i.get('607') and i['607']['value'] or 0.0 for i in depreciations_table])
        total_depreciation = sum([i.get('608') and i['608']['value'] or 0.0 for i in depreciations_table])
        total_book_value_end = sum([i.get('609') and i['609']['value'] or 0.0 for i in depreciations_table])
        totals_depreciations_table = {
            '610': {'field_type': 'float', 'value': total_book_value_beginning},
            '611': {'field_type': 'float', 'value': total_acquisitions},
            '612': {'field_type': 'float', 'value': total_sales},
            '613': {'field_type': 'float', 'value': total_depreciation},
            '614': {'field_type': 'float', 'value': total_book_value_end}
        }
        # for now, everything is considered business portion; so no private part => 615 not filled in
        if totals_depreciations_table['613']['value'] != 0.00:
            totals_depreciations_table['616'] = {'field_type': 'float', 'value': totals_depreciations_table['613']['value']}

        update_values.update(totals_expenditures_table)
        update_values.update(totals_depreciations_table)
        return update_values, expenditures_table, depreciations_table

    def l10n_lu_open_report_export_wizard(self, options):
        """ Creates a new export wizard for this report."""
        new_context = self.env.context.copy()
        new_context['account_report_generation_options'] = options
        return {
            'type': 'ir.actions.act_window',
            'name': _('Export'),
            'view_mode': 'form',
            'res_model': 'l10n_lu.generate.xml',
            'target': 'new',
            'views': [[self.env.ref('l10n_lu_reports_electronic_xml_2_0.view_l10n_lu_generate_xml').id, 'form']],
            'context': new_context,
        }
