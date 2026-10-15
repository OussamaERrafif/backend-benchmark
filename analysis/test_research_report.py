"""Regression coverage for safety-boundary cells that stopped report generation."""
import json
import unittest
import pandas as pd
from process_results import ROOT
from research_report import table, render_html


class ReportTableTests(unittest.TestCase):
    def test_actual_safety_boundary_renders_all_repetition_states(self):
        boundaries=sorted((ROOT/'results/raw').glob('*/*/*/load-boundary.json'))
        if not boundaries:self.skipTest('A real safety boundary is required')
        boundary=next(json.loads(path.read_text()) for path in boundaries
                      if len(json.loads(path.read_text()).get('states',[]))>1)
        markdown=table(pd.DataFrame([boundary]),['target_rps','states','rule'])
        self.assertIn(', '.join(boundary['states']),markdown)
        self.assertIn(boundary['rule'],markdown)
        rendered=render_html(markdown,'RUNNING')
        self.assertIn('<table>',rendered)
        self.assertIn(', '.join(boundary['states']),rendered)

    def test_scalar_missing_values_and_float_format_stay_readable(self):
        frame=pd.DataFrame({'value':[None,float('nan'),pd.NA,1.25,False]},dtype=object)
        markdown=table(frame,['value'])
        self.assertEqual(markdown.count('| — |'),3)
        self.assertIn('| 1.250 |',markdown)
        self.assertIn('| False |',markdown)

    def test_single_empty_and_escaped_state_lists(self):
        frame=pd.DataFrame({'states':[['PASS'],[],('ERROR_LIMIT','LATENCY_LIMIT'),['error | detail\nnext']]})
        markdown=table(frame,['states'])
        self.assertIn('| PASS |',markdown)
        self.assertIn('| — |',markdown)
        self.assertIn('| ERROR_LIMIT, LATENCY_LIMIT |',markdown)
        self.assertIn('| error \\| detail next |',markdown)

    def test_non_scalar_cell_does_not_use_array_truth_value(self):
        markdown=table(pd.DataFrame({'value':[pd.Series(['PASS','ERROR_LIMIT'])]}),['value'])
        self.assertIn('PASS',markdown)
        self.assertIn('ERROR_LIMIT',markdown)


if __name__=='__main__':unittest.main()
