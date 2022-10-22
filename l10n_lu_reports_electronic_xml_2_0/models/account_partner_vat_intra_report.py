# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import datetime
from dateutil.relativedelta import relativedelta
from lxml import etree, objectify
from odoo import fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare

class ReportL10nLuPartnerVatIntra(models.AbstractModel):
    _inherit = "l10n.lu.report.partner.vat.intra"

    def _get_reports_buttons(self):
        res = super()._get_reports_buttons()
        if self._is_lu_electronic_report():
            for re in res:
                if re.get('action') == 'print_xml':
                    # deactivate xml export & saving and allow export of the XML declaration from the wizard
                    re['name'] = _('EXPORT ECDF DECLARATION')
                    re['action'] = 'l10n_lu_open_report_export_wizard'
                    del re['file_export_type']
        return res

    def _get_lu_xml_2_0_report_values(self, options, comparison_files):
        """
        Returns the formatted forms for the LU VAT recapitulative statements (Intracommunity exchange of goods and services).
        This comprises the recapitulative statement of IC supplies of goods (if selected in the options) and
        the recapitulative statement of IC supplies of services (if selected in the options).
        Both declarations comprise corrections tables, which can be used to correct the data reported in previous declarations.
        These tables are filled in by comparing what would be reported at present time with the given comparison declarations.
        (IC supplies of goods (monthly): https://ecdf-developer.b2g.etat.lu/ecdf/forms/popup/TVA_LICM_TYPE/2020M01/en/1/preview
         IC supplies of services (monthly): https://ecdf-developer.b2g.etat.lu/ecdf/forms/popup/TVA_PSIM_TYPE/2020M01/en/1/preview)

        :param comparison_files: past declarations to check and correct if needed
        """
        xml_data, month, quarter, year = super(ReportL10nLuPartnerVatIntra, self)._get_report_data(options)

        # Format the values for the export
        intrastat_codes = [c['name'] for c in options['intrastat_code'] if c['selected']] or ['L', 'T', 'S']
        corrections, compared_declarations = self._get_correction_data(options, comparison_files)
        forms = self._format_export_values(intrastat_codes, xml_data, corrections, compared_declarations, month, quarter, year)

        for form in forms:
            form.update({
                'year': year,
                'period': int(month or quarter),
                'currency': self.env.company.currency_id.name,
                'model': '1',
            })
        return forms, year, month and 'M' + str(month) or quarter and 'Q' + str(quarter), ''.join(intrastat_codes)

    def l10n_lu_open_report_export_wizard(self, options):
        """ Creates a new export wizard for this report."""
        new_context = self.env.context.copy()
        new_context['report_generation_options'] = options
        return {
            'type': 'ir.actions.act_window',
            'name': _('Export'),
            'view_mode': 'form',
            'res_model': 'l10n_lu.generate.vat.intra.report',
            'target': 'new',
            'views': [[self.env.ref('l10n_lu_reports_electronic_xml_2_0.view_l10n_lu_generate_vat_intra_report').id, 'form']],
            'context': new_context,
        }

    def _format_export_values(self, intrastat_codes, xml_data, corrections, compared_declarations, month, quarter, year):
        """
        Returns the formatted forms for the LU VAT recapitulative statements (Intracommunity exchange of goods and services).

        :param intrastat_codes: the codes of the tables to fill in ('L': goods, 'T': triangular operations, 'S': services)
        :param xml_data: the data for the report of the selected period (from _get_lines)
        :para corrections: corrections for previous declarations to include in the report
        :param s: tuple (year, month/quarter, declaration_type) describing the previous declarations that are to be corrected
        :param month: the month of the declared period (if a monthly period is declared)
        :param quarter: the quarter of the declared period (if a quarterly period is declared)
        :param year: the year of the declared period
        """
        def _get_decl_table(lines, total, mapping):
            table = [{
                mapping['country']: {'field_type': 'char', 'value': line[0]},
                mapping['vat']: {'field_type': 'char', 'value': line[1]},
                mapping['amount']: {'field_type': 'number', 'value': line[3]},
            } for line in lines]
            tot = {mapping['total']: {'field_type': 'number', 'value': total}}
            return table, tot

        def _get_corrective_table(corr, is_triangular, language):
            corr_table = []
            for correction_time, line in corr.items():
                period_field = '12' if correction_time[0][-1] == 'T' else '18'
                for vat_info, amount in line.items():
                    corr_table.append({
                        '09': {'field_type': 'char', 'value': vat_info[0]},  # Partner Country
                        '10': {'field_type': 'char', 'value': vat_info[1]},  # Partner VAT number
                        '11': {'field_type': 'number', 'value': correction_time[1]},  # Year
                        period_field: {'field_type': 'number', 'value': correction_time[2]},  # Period
                        '14': {'field_type': 'float', 'value': amount},
                    })
                    if is_triangular:
                        corr_table[-1]['15'] = {'field_type': 'char', 'value': language == 'DE' and 'Ja' or language == 'FR' and 'Oui' or 'Yes'}
            return corr_table

        forms = []
        language = self.env.context['report_generation_options'].get('language')

        # The correction of reports having the same declaring period isn't allowed,
        # and the correction of reports having later declaring periods doesn't make sense
        for cname, ctype, cyear, cperiod in compared_declarations:
            # convert quarters to months to compare; because any overlap has to be avoided,
            # take last month of the quarter of the compared declarations,
            # and the first of the present declaration
            cperiodmonth = int(cperiod) * 3 if ctype[-1] == 'T' else int(cperiod)
            dperiod = int(month) if month else quarter * 3 - 2
            if int(cyear) > int(year) or cperiodmonth >= dperiod and int(cyear) == int(year):
                raise(ValidationError(_("Some problems with the comparison declarations occured. "
                                        "The comparison declarations must refer to earlier periods.")))

        # L & T
        if 'L' in intrastat_codes or 'T' in intrastat_codes:
            declaration_type = month and 'TVA_LICM' or 'TVA_LICT'
            form = {'declaration_type': declaration_type, 'tables': [], 'field_values': {}}
            # L declaration
            mapping = {'total': '04', 'country': '01', 'vat': '02', 'amount': '03'}
            l_table, l_tot = _get_decl_table(xml_data['l_lines'], xml_data['l_sum'], mapping)
            if xml_data['l_lines']:
                form['tables'].append(l_table)
            form['field_values'].update(l_tot)
            # T declaration
            mapping = {'total': '08', 'country': '05', 'vat': '06', 'amount': '07'}
            t_table, t_tot = _get_decl_table(xml_data['t_lines'], xml_data['t_sum'], mapping)
            if xml_data['t_lines']:
                form['tables'].append(t_table)
            form['field_values'].update(t_tot)
            # Corrections
            total_corrections = 0.00
            if corrections:
                corr_tables = []
                if 'L' in intrastat_codes:
                    corr_tables.extend(_get_corrective_table(corrections['l_lines'], is_triangular=False, language=language))
                if 'T' in intrastat_codes:
                    corr_tables.extend(_get_corrective_table(corrections['t_lines'], is_triangular=True, language=language))
                if corr_tables:
                    form['tables'].append(corr_tables)
                total_corrections = corrections['l_sum'] * int('L' in intrastat_codes) + corrections['t_sum'] * int('T' in intrastat_codes)
            form['field_values']['16'] = {'field_type': 'float', 'value': total_corrections}
            forms.append(form)

        # S
        if 'S' in intrastat_codes:
            declaration_type = month and 'TVA_PSIM' or 'TVA_PSIT'
            form = {'declaration_type': declaration_type, 'tables': [], 'field_values': {}}
            mapping = {'total': '04', 'country': '01', 'vat': '02', 'amount': '03'}
            s_table, s_tot = _get_decl_table(xml_data['s_lines'], xml_data['s_sum'], mapping)
            if xml_data['s_lines']:
                form['tables'].append(s_table)
            form['field_values'].update(s_tot)
            # Corrections
            if corrections:
                s_corr_table = _get_corrective_table(corrections['s_lines'], is_triangular=False, language=language)
                total_corrections = corrections['s_sum'] if corrections else 0.00
                form['field_values']['16'] = {'field_type': 'float', 'value': corrections['s_sum']}
                if s_corr_table:
                    form['tables'].append(s_corr_table)
            forms.append(form)

        return forms

    def _get_correction_data(self, options, comparison_files):
        """
        Compares the data from old declarations to the data that would be reported at this time.

        :param comparison_files(strings): the eCDF-formatted previous declarations to check and correct
        :return
            corrections: a dictionary
                - x_sum: <sum of all corrections for l_lines>
                - x_lines: {(decl_type, decl_year, decl_period): {(acquirer_country, acquirer_vat): <correction_amount>, ..}, ..}
                for x in l, t, s
            original_declarations: a list of tuples (year, month/quarter, declaration_type)
                                   describing the relevant declarations contained in the comparison files
        """
        attached_declarations = []
        for name, dec in comparison_files:
            try:
                attached_declarations.append((name, self._get_data_from_xml(dec, self.env.company.matr_number)))
            except ValidationError as err:
                raise(ValidationError(_("Error in file ") + name + ": " + str(err)))
        summarized_data, original_declarations = self._summarize_data(attached_declarations)
        corrections = self._compare_declarations(summarized_data, options)
        return corrections, original_declarations

    # Comparison methods
    def _get_data_from_xml(self, string, company_matr):
        """
        Gets the VAT Intra declarations data from an ecdf-compliant formatted xml declaration,
        for the company with the indicated Matr. number.

        :param xml_file: the file to be parsed
        :param company_matr
        :return: a data dictionary with:
            - 'original': lines originally declared in the declaration
            - 'corrective': lines that correct a previous declaration
            - 'declared': original declarations
            Each line a dictionary with keys: type, year, period, acquirer, triangular
        """
        fields_map = {
            ('03', '07', '14'): 'amount', ('07', '15'): 'triangular', ('01', '05', '09'): 'acquirer_country',
            ('02', '06', '10'): 'acquirer_vat', ('11'): 'year', ('12', '18'): 'period',
            ('12'): 'quarterly', ('18'): 'monthly',
        }
        prfx = '{http://www.ctie.etat.lu/2011/ecdf}'
        try:
            root = objectify.fromstring(string)
        except (etree.XMLSyntaxError):
            raise ValidationError(_("The provided comparison file is not a properly formatted XML."))
        data = {'original': [], 'corrective': [], 'declared': []}
        try:
            # Element Declarer
            declarations = [d for d in root.iterchildren(tag=prfx + 'Declarations')]
            declarer = [declarer for d in declarations for declarer in d.iterchildren(tag=prfx + 'Declarer')]
            # Filter for company with the right company Matr.nb and of IC type
            declarations = []
            for decl in declarer:
                if decl.MatrNbr.text == company_matr:
                    for d in decl.iterchildren(tag=prfx + 'Declaration'):
                        if d.get('type') in ('TVA_LICT', 'TVA_LICM', 'TVA_PSIT', 'TVA_PSIM'):
                            declarations.append(d)
            for decl in declarations:
                year = decl.Year.text
                period = decl.Period.text
                dtype = decl.attrib['type']
                # Don't care about the totals, only values in tables
                tables = [table for table in decl.FormData.iterchildren(tag=prfx + 'Table')]
                for line in [line for table in tables for line in table.iterchildren(tag=prfx + 'Line')]:
                    line_data = {}
                    for field_type in ('TextField', 'NumericField'):
                        for field in line.iterchildren(tag=prfx + field_type):
                            for key in [key for key in fields_map if field.attrib['id'] in key]:
                                line_data[fields_map[key]] = field.text
                    corrective = 'year' in line_data
                    line_data['triangular'] = bool(line_data.get('triangular', False))
                    if not corrective:
                        line_data['year'] = year
                        line_data['period'] = period
                        line_data['type'] = dtype
                    else:
                        line_data['type'] = dtype[:-1] + ('T' if line_data.get('quarterly', False) else 'M')
                    line_processed = {k: line_data[k] for k in ('type', 'year', 'period', 'acquirer_country', 'acquirer_vat', 'triangular', 'amount')}
                    if corrective:
                        data['corrective'].append(line_processed)
                    else:
                        data['original'].append(line_processed)
                data['declared'].append({'year': year, 'period': period, 'type': dtype})
        except:
            raise ValidationError(_("The provided comparison file is not a proper eCDF XML declaration!"))
        # if no interesting data has been found in the declarations, then probably the user made a mistake
        if not data['corrective'] and not data['original'] and not data['declared']:
            raise ValidationError(_("There are no Intracommunity VAT declarations for the declaring company in the provided file!"))
        return data

    def _summarize_data(self, declarations):
        """
        Summarizes the declaration data from multiple declarations.
        Form: {(type, year, period): {'s_lines': {(acquirer_country: x, acquirer_vat: y): <amount>, ..}, 't_lines': {..}, 's_lines': {..}}, ..}
        """
        original_declarations = [(d['type'], d['year'], d['period']) for decl in declarations for d in decl[1]['declared']]
        summarized_data = {key: {'s_lines': {}, 't_lines': {}, 'l_lines': {}} for key in original_declarations}
        # Iterate over all lines whose original declaration is present in the given declarations;
        # this allows, for example, correcting just the previous declaration by comparing with it,
        # without taking into consideration the corrections that it made to previous declarations
        for line in [ln for data in declarations for ln in (data[1]['original'] + data[1]['corrective'])]:
            line_decl = (line['type'], line['year'], line['period'])
            if line_decl in original_declarations:
                line_type = 's_lines' if line['type'][4] == 'P' else line['triangular'] and 't_lines' or 'l_lines'
                acq = (line['acquirer_country'], line['acquirer_vat'])
                summarized_data[line_decl][line_type][acq] = summarized_data[line_decl][line_type].get(acq, 0.00) + float(line['amount'].replace(',', '.'))
        # Empty declarations (no data for the declared period) need to be considered too
        for data in original_declarations:
            if data not in summarized_data.keys():
                summarized_data[data] = {'s_lines': {}, 'l_lines': {}, 't_lines': {}}
        # Need the filename as well to be able to raise precise error messages
        original_declarations = [(decl[0], d['type'], d['year'], d['period']) for decl in declarations for d in decl[1]['declared']]
        return summarized_data, original_declarations

    def _compare_declarations(self, summarized_data, options):
        """
        Compares the data from summarized data to the data that would be reported at this time.
        :param summarized_data: data in the form coming from _summarize_data
        :return: dictionary:
            - x_sum: <sum of all corrections for l_lines>
            - x_lines: {(decl_type, decl_year, decl_period): {(acquirer_country, acquirer_vat): <correction_amount>, ..}, ..}
            for x in l, t, s
        """
        def _compare_lines(old, new):
            corrections = {}
            for k in new:
                corr = float(new[k].replace(',', '.')) - old.get(k, 0.00)
                if float_compare(corr, 0.0, 2) != 0:
                    corrections[k] = corr
            for k in old:
                if k not in new:
                    if float_compare(old[k], 0.0, 2) != 0:
                        corrections[k] = - old[k]
            return corrections

        corrections = {'l_lines': {}, 't_lines': {}, 's_lines': {}}
        for key in summarized_data:  # key: (declaration_type, year, period)
            decl_type, decl_year, decl_period = key
            # Compute declaration dates
            decl_day_from = 1
            decl_month_from = int(decl_period) if decl_type[-1] == 'M' else 1 + 3 * (int(decl_period) - 1)
            decl_date_from = datetime(int(decl_year), decl_month_from, decl_day_from)
            delta = relativedelta(months=1, days=-1) if decl_type[-1] == 'M' else relativedelta(months=3, days=-1)
            decl_date_to = decl_date_from + delta
            options['date'] = {'mode': 'range', 'date_from': decl_date_from, 'date_to': decl_date_to}
            # Get the actualised data for the examined period
            ctx = self._set_context(options)
            ctx.update({'no_format': True, 'get_xml_data': True})
            new_lines = self.with_context(ctx)._get_lines(options)
            new_data = {ln_type: {(k[0], k[1]): k[3] for k in decl} for ln_type, decl in new_lines.items() if ln_type.endswith('lines')}
            for k in new_data:
                if k[0] in ['l', 't'] and key[0][4] == 'L' or k[0] == 's' and key[0][4] == 'P':
                    corrections[k][key] = _compare_lines(summarized_data[key].get(k, {}), new_data[k])
                else:
                    corrections[k][key] = {}
        # compute the sums of the corrections for each line type
        correction_sums = {}
        for k in corrections.keys():
            correction_sums[k.replace('lines', 'sum')] = sum([sum(corrections[k][key].values()) for key in corrections[k]])
        corrections.update(correction_sums)
        return corrections


class L10n_luStoredIntraReport(models.Model):
    _name = 'l10n_lu.stored.intra.report'
    _description = "Wrapper for an attachment, adds the financial report data"
    _rec_name = "display_name"

    attachment_id = fields.Many2one(comodel_name='ir.attachment')
    year = fields.Char(required=True)
    period = fields.Char(required=True)
    codes = fields.Selection([
        ('LT', 'Supply of goods and supply of goods made in the context of triangular operations'),
        ('S', 'Supply of services'),
        ('LTS', 'Supply of goods (normal/in the context of triangular operations) and supply of services')
    ])
    company_id = fields.Many2one(comodel_name='res.company', default=lambda self: self.env.company.id)

    def name_get(self):
        result = []
        for r in self:
            result.append((r.id, r.year + '/' + r.period + '/' + r.codes + ' : ' + r.attachment_id.name))
        return result
