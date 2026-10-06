import copy
import json
from pathlib import Path
import unittest

from experiments.quote_review import compare, revision_payload
import test_deal_revisions as revision_fixtures


class QuoteReviewExperimentTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads((Path(__file__).parents[1] / 'experiments/quote_review.synthetic.json').read_text())

    def test_headline_price_is_not_reviewed_cost_and_no_winner_is_invented(self):
        a, b = compare(self.document)
        self.assertEqual((a['quoted_total'], b['quoted_total']), (14000, 18000))
        self.assertEqual((a['reviewed_amount_before_project_reserve'], b['reviewed_amount_before_project_reserve']), (20500, 19300))
        self.assertNotIn('winner', a)

    def test_unresolved_work_has_no_final_total_and_cannot_be_applied(self):
        row = self.document['options'][0]['rows'][0]
        row.update(status='not_found', evidence='', resolution='unresolved', additional_amount=None)
        report = compare(self.document)[0]
        self.assertIsNone(report['reviewed_amount_before_project_reserve'])
        self.assertIn('Demolition and disposal', report['unresolved'])
        with self.assertRaisesRegex(ValueError, 'Resolve these costs'):
            revision_payload(self.document, 'contractor-a')

    def test_missing_and_zero_cost_are_not_silently_valid_allowances(self):
        for amount in (None, 0, -1, 'NaN', 'Infinity', True, 1.001):
            with self.subTest(amount=amount):
                data = copy.deepcopy(self.document)
                data['options'][0]['rows'][0]['additional_amount'] = amount
                with self.assertRaises(ValueError):
                    compare(data)

    def test_quote_evidence_must_exist_in_supplied_text(self):
        self.document['options'][0]['rows'][0]['evidence'] = 'Made-up inclusion not in this quote.'
        with self.assertRaisesRegex(ValueError, 'excerpt is not'):
            compare(self.document)

    def test_user_confirmation_is_distinct_from_quote_text(self):
        self.document['options'][0]['rows'][0].update(evidence_origin='user_confirmation',
            evidence='Contractor confirmed disposal remains excluded.', locator='Synthetic call note, 2026-09-11')
        self.assertEqual(compare(self.document)[0]['evidence'][0]['origin'], 'user_confirmation')

    def test_excluded_work_cannot_be_declared_part_of_quote_without_resolution(self):
        self.document['options'][0]['rows'][0].update(resolution='in_quote', additional_amount=None)
        with self.assertRaisesRegex(ValueError, 'cannot be treated as included'):
            compare(self.document)

    def test_existing_cost_must_be_retained_and_is_not_added_twice(self):
        row = self.document['options'][0]['rows'][0]
        row.update(resolution='elsewhere', additional_amount=None, existing_scope_item_id='roof',
                   resolution_note='Synthetic test: retained roof allowance also covers disposal.')
        self.assertEqual(compare(self.document)[0]['reviewed_amount_before_project_reserve'], 17500)
        row['existing_scope_item_id'] = 'kitchen'
        with self.assertRaisesRegex(ValueError, 'link a retained'):
            compare(self.document)

    def test_all_topics_overlap_and_contingency_need_review(self):
        for mutation in ('duplicate', 'overlap', 'contingency'):
            data = copy.deepcopy(self.document)
            if mutation == 'duplicate': data['options'][0]['rows'][0]['id'] = 'tops'
            if mutation == 'overlap': data['overlap_reviewed'] = False
            if mutation == 'contingency': data['contingency_review_note'] = ''
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compare(data)

    def test_revision_preserves_baseline_and_counts_reserve_once(self):
        original = copy.deepcopy(self.document)
        body = revision_payload(self.document, 'contractor-b')
        self.assertEqual(self.document, original)
        self.assertEqual(body['parent_deal_id'], 1)
        self.assertEqual(body['draft_input']['rehab_budget']['value'], 34430)
        self.assertEqual(len(body['rehab_scope']['items']), 3)
        quote = body['rehab_scope']['items'][1]
        self.assertIn('Finish repairs by owner.', quote['notes'])
        self.assertEqual(body['rehab_scope']['items'][2]['basis'], 'allowance')

    def test_proposed_revision_roundtrips_through_actual_owner_checked_save_api(self):
        fixture = revision_fixtures.RevisionTests(methodName='runTest')
        fixture.setUp()
        try:
            baseline = copy.deepcopy(self.document['baseline'])
            baseline.pop('id')
            baseline['analysis_result'] = {}
            original = fixture.save(baseline)
            self.document['baseline'] = original
            proposed = revision_payload(self.document, 'contractor-b')
            denied = fixture.client.post('/api/deals/save', json=proposed, headers={'X-Test-User': 'bob'})
            self.assertEqual(denied.status_code, 404)
            revised = fixture.save(proposed)
            self.assertEqual(fixture.client.get(f'/api/deals/{original["id"]}').json(), original)
            self.assertEqual(fixture.client.get(f'/api/deals/{revised["id"]}').json(), revised)
            self.assertEqual(revised['parent_deal_id'], original['id'])
            self.assertEqual(revised['draft_input']['rehab_budget']['value'], 34430)
            self.assertEqual(revised['analysis_result'], proposed['analysis_result'])
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
