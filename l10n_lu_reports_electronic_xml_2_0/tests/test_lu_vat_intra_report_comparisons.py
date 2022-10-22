# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
from odoo.addons.account_reports.tests.common import TestAccountReportsCommon
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import Form

@tagged('post_install', '-at_install')
class LuxembourgVatIntraReportComparisonsTest(TestAccountReportsCommon):

    @classmethod
    def setUpClass(cls, chart_template_ref='l10n_lu.lu_2011_chart_1'):
        super().setUpClass(chart_template_ref=chart_template_ref)

        cls.company_data['company'].ecdf_prefix = '1234AB'
        cls.company_data['company'].matr_number = '1111111111111'

        cls.l_tax = cls.env['account.tax'].search([('company_id', '=', cls.company_data['company'].id), ('name', '=', '0-IC-S-G'), '|', ("active", "=", True), ("active", "=", False)])
        cls.t_tax = cls.env['account.tax'].search([('company_id', '=', cls.company_data['company'].id), ('name', '=', '0-ICT-S-G'), '|', ("active", "=", True), ("active", "=", False)])
        cls.s_tax = cls.env['account.tax'].search([('company_id', '=', cls.company_data['company'].id), ('name', '=', '0-IC-S-S'), '|', ("active", "=", True), ("active", "=", False)])
        cls.l_tax.active = cls.t_tax.active = cls.s_tax.active = True

        cls.product_1 = cls.env['product.product'].create({'name': 'product_1', 'lst_price': 1.0})
        cls.product_2 = cls.env['product.product'].create({'name': 'product_2', 'lst_price': 10.0})
        cls.product_3 = cls.env['product.product'].create({'name': 'product_3', 'lst_price': 100.0})
        cls.partner_be = cls.env['res.partner'].create({
            'name': 'Partner BE',
            'country_id': cls.env.ref('base.be').id,
            'vat': 'BE0477472701',
        })
        cls.partner_be_new = cls.env['res.partner'].create({
            'name': 'Partner BE New',
            'country_id': cls.env.ref('base.be').id,
            'vat': 'BE0507741055',
        })
        cls.partner_fr = cls.env['res.partner'].create({
            'name': 'Partner FR',
            'country_id': cls.env.ref('base.fr').id,
            'vat': 'FR00000000190',
        })
        cls.partner_lu = cls.env['res.partner'].create({
            'name': 'Partner LU',
            'country_id': cls.env.ref('base.lu').id,
            'vat': 'LU12345613',
        })
        invoice_dates = ['2020-04-01', '2020-05-23', '2020-06-12']
        invoices = [
            {'partner': cls.partner_be, 'product': cls.product_1, 'tax': cls.l_tax},
            {'partner': cls.partner_be, 'product': cls.product_2, 'tax': cls.t_tax},
            {'partner': cls.partner_be, 'product': cls.product_3, 'tax': cls.s_tax},
        ]
        for date in invoice_dates:
            cls.post_example_invoices(invoices, date)

    def test_empty_comparisons(self):
        report = self.env['l10n.lu.report.partner.vat.intra']
        report = report.with_context(
            date_from='2020-04-01',
            date_to='2020-04-30'
        )
        options = report._get_options(None)
        options['intrastat_code'] = [
            {'id': 'Shipment', 'name': 'L', 'selected': False, 'lines': self.env.ref('l10n_lu.account_tax_report_line_1b_1_intra_community_goods_pi_vat').ids},
            {'id': 'Triangular', 'name': 'T', 'selected': False, 'lines': self.env.ref('l10n_lu.account_tax_report_line_1b_6_a_subsequent_to_intra_community').ids},
            {'id': 'Services', 'name': 'S', 'selected': False, 'lines': self.env.ref('l10n_lu.account_tax_report_line_1b_6_b1_non_exempt_customer_vat').ids},
        ]
        corrections, compared_declarations = report._get_correction_data(options, comparison_files=[])
        self.assertFalse(compared_declarations)
        self.assertFalse(any([bool(c) for c in corrections.values()]))

    def test_multiple_comparison(self):
        # Case 1: correct a previous declaration
        report = self.env['l10n.lu.report.partner.vat.intra']
        report, options = self._get_report_and_options(report, '2020-04-01', '2020-04-30')
        wizard = self.env['l10n_lu.generate.vat.intra.report'].create({})
        wizard.save_report = False
        wizard.with_context(report.l10n_lu_open_report_export_wizard(options)['context']).get_xml()
        declaration_to_compare = base64.b64decode(wizard.report_data.decode("utf-8"))

        invoices = [
            {'partner': self.partner_be, 'product': self.product_1, 'tax': self.l_tax},
            {'partner': self.partner_be_new, 'product': self.product_1, 'tax': self.l_tax},
            {'partner': self.partner_be, 'product': self.product_2, 'tax': self.t_tax},
            {'partner': self.partner_be_new, 'product': self.product_2, 'tax': self.t_tax},
            {'partner': self.partner_be, 'product': self.product_3, 'tax': self.s_tax},
            {'partner': self.partner_be_new, 'product': self.product_3, 'tax': self.s_tax},
        ]
        self.post_example_invoices(invoices, '2020-04-14')

        report = self.env['l10n.lu.report.partner.vat.intra']
        report, options = self._get_report_and_options(report, '2020-05-01', '2020-05-31')
        wizard2 = self.env['l10n_lu.generate.vat.intra.report'].create({})
        wizard2.save_report = False
        attachment = self.env['ir.attachment'].create({'datas': wizard.report_data, 'name': 'discard'})
        stored_report = self.env['l10n_lu.stored.intra.report'].create({'attachment_id': attachment.id, 'year': '2020', 'period': '5', 'codes': 'LTS'})
        wizard2.l10n_lu_stored_report_ids = stored_report
        wizard2.with_context(report.l10n_lu_open_report_export_wizard(options)['context']).get_xml()
        declaration_to_compare_2 = base64.b64decode(wizard2.report_data.decode("utf-8"))
        filename = wizard2.filename[:-4]  # remove '.xml' postfix
        expected_xml_tree = self.get_xml_tree_from_string(
            f'''
                <eCDFDeclarations xmlns="http://www.ctie.etat.lu/2011/ecdf">
                    <FileReference>{filename}</FileReference>
                    <eCDFFileVersion>2.0</eCDFFileVersion>
                    <Interface>MODL5</Interface>
                    <Agent>
                        <MatrNbr>1111111111111</MatrNbr>
                        <RCSNbr>NE</RCSNbr>
                        <VATNbr>NE</VATNbr>
                    </Agent>
                    <Declarations>
                        <Declarer>
                            <MatrNbr>1111111111111</MatrNbr>
                            <RCSNbr>NE</RCSNbr>
                            <VATNbr>NE</VATNbr>
                            <Declaration type="TVA_LICM" model="1" language="EN">
                                <Year>2020</Year>
                                <Period>5</Period>
                                <FormData>
                                    <NumericField id="04">1,00</NumericField>
                                    <NumericField id="08">10,00</NumericField>
                                    <NumericField id="16">22,00</NumericField>
                                    <Table>
                                        <Line num="1">
                                            <TextField id="01">BE</TextField>
                                            <TextField id="02">0477472701</TextField>
                                            <NumericField id="03">1,00</NumericField>
                                        </Line>
                                    </Table>
                                    <Table>
                                        <Line num="1">
                                            <TextField id="05">BE</TextField>
                                            <TextField id="06">0477472701</TextField>
                                            <NumericField id="07">10,00</NumericField>
                                        </Line>
                                    </Table>
                                    <Table>
                                        <Line num="1">
                                            <TextField id="09">BE</TextField>
                                            <TextField id="10">0477472701</TextField>
                                            <NumericField id="11">2020</NumericField>
                                            <NumericField id="18">4</NumericField>
                                            <NumericField id="14">1,00</NumericField>
                                        </Line>
                                        <Line num="2">
                                            <TextField id="09">BE</TextField>
                                            <TextField id="10">0507741055</TextField>
                                            <NumericField id="11">2020</NumericField>
                                            <NumericField id="18">4</NumericField>
                                            <NumericField id="14">1,00</NumericField>
                                        </Line>
                                        <Line num="3">
                                            <TextField id="09">BE</TextField>
                                            <TextField id="10">0477472701</TextField>
                                            <NumericField id="11">2020</NumericField>
                                            <NumericField id="18">4</NumericField>
                                            <NumericField id="14">10,00</NumericField>
                                            <TextField id="15">Yes</TextField>
                                        </Line>
                                        <Line num="4">
                                            <TextField id="09">BE</TextField>
                                            <TextField id="10">0507741055</TextField>
                                            <NumericField id="11">2020</NumericField>
                                            <NumericField id="18">4</NumericField>
                                            <NumericField id="14">10,00</NumericField>
                                            <TextField id="15">Yes</TextField>
                                        </Line>
                                    </Table>
                                </FormData>
                            </Declaration>
                            <Declaration type="TVA_PSIM" model="1" language="EN">
                                <Year>2020</Year>
                                <Period>5</Period>
                                <FormData>
                                    <NumericField id="04">100,00</NumericField>
                                    <NumericField id="16">200,00</NumericField>
                                    <Table>
                                        <Line num="1">
                                            <TextField id="01">BE</TextField>
                                            <TextField id="02">0477472701</TextField>
                                            <NumericField id="03">100,00</NumericField>
                                        </Line>
                                    </Table>
                                    <Table>
                                        <Line num="1">
                                            <TextField id="09">BE</TextField>
                                            <TextField id="10">0477472701</TextField>
                                            <NumericField id="11">2020</NumericField>
                                            <NumericField id="18">4</NumericField>
                                            <NumericField id="14">100,00</NumericField>
                                        </Line>
                                        <Line num="2">
                                            <TextField id="09">BE</TextField>
                                            <TextField id="10">0507741055</TextField>
                                            <NumericField id="11">2020</NumericField>
                                            <NumericField id="18">4</NumericField>
                                            <NumericField id="14">100,00</NumericField>
                                        </Line>
                                    </Table>
                                </FormData>
                            </Declaration>
                        </Declarer>
                    </Declarations>
                </eCDFDeclarations>
            '''
        )
        self.assertXmlTreeEqual(self.get_xml_tree_from_string(declaration_to_compare_2), expected_xml_tree)

        self.post_example_invoices(invoices, '2020-04-14')
        self.post_example_invoices(invoices, '2020-05-14')

        # Case 2: correct a previous declaration (05), which contains corrections for an earlier declaration
        # but the corrected declaration is not in the comparison files, hence it shouldn't be corrected
        report, options = self._get_report_and_options(report, '2020-06-01', '2020-06-30')
        corrections, compared_declarations = report._get_correction_data(options, comparison_files=[('', declaration_to_compare_2)])
        self.assertListEqual(compared_declarations, [('', 'TVA_LICM', '2020', '5'), ('', 'TVA_PSIM', '2020', '5')])
        expected = {
            'l_sum': 2.0,
            't_sum': 20.0,
            's_sum': 200.0,
            'l_lines': {('TVA_LICM', '2020', '5'): {('BE', '0477472701'): 1.0, ('BE', '0507741055'): 1.0},
                        ('TVA_PSIM', '2020', '5'): {}},
            't_lines': {('TVA_LICM', '2020', '5'): {('BE', '0477472701'): 10.0, ('BE', '0507741055'): 10.0},
                        ('TVA_PSIM', '2020', '5'): {}},
            's_lines': {('TVA_PSIM', '2020', '5'): {('BE', '0477472701'): 100.0, ('BE', '0507741055'): 100.0}, 
                        ('TVA_LICM', '2020', '5'): {}}
        }
        self.assertDictEqual(expected, corrections, 'Wrong correction values for Luxembourg VAT Intra report.')

        # Case 3: declaration (5) to correct + declaration (4) to correct and has corrections in (5);
        # then the correction for (4) should take into account the corrections in (5)
        # Case 2: correct a previous declaration (05), which contains corrections for an earlier declaration
        # but the corrected declaration is not in the comparison files, hence it shouldn't be corrected
        corrections, compared_declarations = report._get_correction_data(
            options, comparison_files=[('', declaration_to_compare), ('', declaration_to_compare_2)]
        )
        self.assertListEqual(compared_declarations, [('', 'TVA_LICM', '2020', '4'), ('', 'TVA_PSIM', '2020', '4'), ('', 'TVA_LICM', '2020', '5'), ('', 'TVA_PSIM', '2020', '5')])
        expected = {
            'l_sum': 4.0,
            't_sum': 40.0,
            's_sum': 400.0,
            'l_lines': {('TVA_LICM', '2020', '4'): {('BE', '0477472701'): 1.0, ('BE', '0507741055'): 1.0},
                        ('TVA_PSIM', '2020', '4'): {},
                        ('TVA_LICM', '2020', '5'): {('BE', '0477472701'): 1.0, ('BE', '0507741055'): 1.0},
                        ('TVA_PSIM', '2020', '5'): {}},
            't_lines': {('TVA_LICM', '2020', '4'): {('BE', '0477472701'): 10.0, ('BE', '0507741055'): 10.0},
                        ('TVA_PSIM', '2020', '4'): {},
                        ('TVA_LICM', '2020', '5'): {('BE', '0477472701'): 10.0, ('BE', '0507741055'): 10.0},
                        ('TVA_PSIM', '2020', '5'): {}},
            's_lines': {('TVA_PSIM', '2020', '4'): {('BE', '0477472701'): 100.0, ('BE', '0507741055'): 100.0},
                        ('TVA_LICM', '2020', '4'): {},
                        ('TVA_PSIM', '2020', '5'): {('BE', '0477472701'): 100.0, ('BE', '0507741055'): 100.0},
                        ('TVA_LICM', '2020', '5'): {}}
        }
        self.assertDictEqual(expected, corrections, 'Wrong correction values for Luxembourg VAT Intra report.')
        return

    def test_wrong_files(self):
        # Case 1: not ecdf declaration
        report = self.env['l10n.lu.report.partner.vat.intra']
        report, options = self._get_report_and_options(report, '2020-04-01', '2020-04-30')
        with self.assertRaises(ValidationError):
            corrections, compared_declarations = report._get_correction_data(options, comparison_files=[('', '')])
        # Case 2: ecdf declaration without VAT Intra declarations inside
        asset_report = self.env['account.assets.report'].with_context({'model': 'account.assets.report'})
        options = asset_report._get_options(None)
        wizard = self.env['l10n_lu.generate.xml'].create({})
        wizard.with_context(asset_report.l10n_lu_open_report_export_wizard(options)['context']).get_xml()
        declaration_to_compare = base64.b64decode(wizard.report_data.decode("utf-8"))
        with self.assertRaises(ValidationError):
            corrections, compared_declarations = report._get_correction_data(options, comparison_files=[('', declaration_to_compare)])

    def _get_report_and_options(self, report, date_from, date_to):
        report = report.with_context(date_from=date_from, date_to=date_to, lang='EN_us')
        options = report._get_options({'date': {'date_from': date_from, 'date_to': date_to, 'filter': 'custom'}})
        options['intrastat_code'] = [
            {'id': 'Shipment', 'name': 'L', 'selected': True, 'lines': self.env.ref('l10n_lu.account_tax_report_line_1b_1_intra_community_goods_pi_vat').ids},
            {'id': 'Triangular', 'name': 'T', 'selected': True, 'lines': self.env.ref('l10n_lu.account_tax_report_line_1b_6_a_subsequent_to_intra_community').ids},
            {'id': 'Services', 'name': 'S', 'selected': True, 'lines': self.env.ref('l10n_lu.account_tax_report_line_1b_6_b1_non_exempt_customer_vat').ids},
        ]
        return report, options

    @classmethod
    def post_example_invoices(cls, invoices, date):
        for inv in invoices:
            move_form = Form(cls.env['account.move'].with_context(default_move_type='out_invoice'))
            move_form.invoice_date = date
            move_form.partner_id = inv['partner']
            with move_form.invoice_line_ids.new() as line_form:
                line_form.product_id = inv['product']
                line_form.tax_ids.clear()
                line_form.tax_ids.add(inv['tax'])
            move = move_form.save()
            move.action_post()
