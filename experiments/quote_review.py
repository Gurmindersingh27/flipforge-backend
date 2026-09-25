"""Manual quote-review experiment. CLI only; never registers an API route.

Evidence and scope classifications are supplied by a reviewer. This is not a
document parser, missing-scope detector, or claim that a quote is complete.
"""
import argparse
import copy
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from app.models import RehabScope, SaveDealRequest
from app.services.deal_revision_service import canonical_revision, scope_total


def money(value, label):
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label}: an explicit amount is required.")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label}: invalid amount.") from exc
    if not amount.is_finite() or amount < 0 or amount > 100000000:
        raise ValueError(f"{label}: invalid amount.")
    if amount != amount.quantize(Decimal('.01')):
        raise ValueError(f"{label}: use whole cents.")
    return amount


def review_option(document, option):
    baseline = document['baseline']
    scope = RehabScope.model_validate(baseline.get('rehab_scope'))
    replacements = document['replace_item_ids']
    existing = {item.id for item in scope.items}
    if not replacements or len(set(replacements)) != len(replacements) or not set(replacements) <= existing:
        raise ValueError('Choose unique existing scope items to replace; itemize a lump sum first.')
    requirements = document['requirements']
    keys = [item['id'] for item in requirements]
    if not keys or len(set(keys)) != len(keys):
        raise ValueError('Review requirements must have unique IDs.')
    rows = option['rows']
    if len(rows) != len(keys) or {row['id'] for row in rows} != set(keys):
        raise ValueError('Every required scope topic needs exactly one review row.')
    if not option.get('source', '').strip() or not option.get('quote_text', '').strip():
        raise ValueError('Record the quote source and supplied text.')
    date.fromisoformat(option['quote_date'])
    total = money(option.get('quoted_total'), 'Quote total')
    if total <= 0:
        raise ValueError('Quote total must be positive.')
    if not document.get('overlap_reviewed'):
        raise ValueError('Review overlap with the items being replaced and retained.')
    if scope.contingency_pct and not document.get('contingency_review_note', '').strip():
        raise ValueError('Explain how the existing reserve relates to contingency already in the quote.')
    labels = {item['id']: item['label'] for item in requirements}
    unresolved, allowances, evidence = [], [], []
    for row in rows:
        label = labels[row['id']]
        status, resolution = row['status'], row['resolution']
        if status not in {'included', 'excluded', 'not_found', 'not_applicable'}:
            raise ValueError(f'{label}: invalid scope status.')
        excerpt = row.get('evidence', '').strip()
        origin = row.get('evidence_origin')
        locator = row.get('locator', '').strip()
        if status in {'included', 'excluded'}:
            if not excerpt or not locator:
                raise ValueError(f'{label}: quote statements need evidence and a source locator.')
            if origin == 'quote_text':
                if excerpt not in option['quote_text']:
                    raise ValueError(f'{label}: excerpt is not in the supplied quote text.')
            elif origin != 'user_confirmation':
                raise ValueError(f'{label}: distinguish quote text from a user confirmation.')
        note = row.get('resolution_note', '').strip()
        if resolution != 'allowance' and row.get('additional_amount') is not None:
            raise ValueError(f'{label}: additional cost is only allowed on an explicit allowance.')
        if resolution != 'elsewhere' and row.get('existing_scope_item_id'):
            raise ValueError(f'{label}: do not combine an extra allowance with an existing cost.')
        if resolution == 'in_quote':
            if status != 'included':
                raise ValueError(f'{label}: unresolved or excluded work cannot be treated as included.')
        elif resolution == 'allowance':
            if status == 'included' or status == 'not_applicable' or not note:
                raise ValueError(f'{label}: explain the additional allowance and avoid double counting.')
            amount = money(row.get('additional_amount'), label)
            if amount <= 0:
                raise ValueError(f'{label}: unknown work is not a $0 allowance; confirm inclusion or another cost.')
            allowances.append({'id': row['id'], 'label': label, 'amount': amount, 'note': note})
        elif resolution == 'elsewhere':
            if status == 'included' or not note or row.get('existing_scope_item_id') not in existing - set(replacements):
                raise ValueError(f'{label}: link a retained scope item and explain its coverage.')
        elif resolution == 'not_applicable':
            if status == 'included' or not note:
                raise ValueError(f'{label}: record why this work is not applicable.')
        elif resolution == 'unresolved':
            unresolved.append(label)
        else:
            raise ValueError(f'{label}: invalid cost resolution.')
        evidence.append({'topic': label, 'status': status, 'origin': origin or 'reviewer checklist',
                         'excerpt': excerpt, 'locator': locator, 'resolution': resolution, 'note': note})
    known = total + sum((a['amount'] for a in allowances), Decimal(0))
    return {'option_id': option['id'], 'quoted_total': float(total),
            'known_amount_before_project_reserve': float(known),
            'reviewed_amount_before_project_reserve': None if unresolved else float(known),
            'unresolved': unresolved, 'allowances': allowances, 'evidence': evidence}


def compare(document):
    ids = [option['id'] for option in document['options']]
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise ValueError('Supply at least two uniquely identified quote options.')
    # Deliberately no automatic winner: evidence/applicability still need human judgment.
    return [review_option(document, option) for option in document['options']]


def revision_payload(document, selected_id):
    reports = {report['option_id']: report for report in compare(document)}
    if selected_id not in reports:
        raise ValueError('Selected quote was not reviewed.')
    review = reports[selected_id]
    if review['unresolved']:
        raise ValueError('Resolve these costs before applying: ' + ', '.join(review['unresolved']))
    option = next(o for o in document['options'] if o['id'] == selected_id)
    baseline = document['baseline']
    scope = copy.deepcopy(baseline['rehab_scope'])
    scope['items'] = [i for i in scope['items'] if i['id'] not in document['replace_item_ids']]
    evidence = '\n'.join(f"{e['topic']}: {e['status']} [{e['origin']}; {e['locator']}] {e['excerpt']} | {e['resolution']}: {e['note']}" for e in review['evidence'])
    scope['items'].append({'id': str(uuid4()), 'category': 'Reviewed quote', 'description': option['label'],
                           'quantity': 1, 'unit': 'quote', 'unit_cost': review['quoted_total'],
                           'basis': 'quote', 'source': option['source'], 'quote_date': option['quote_date'], 'notes': evidence})
    for allowance in review['allowances']:
        scope['items'].append({'id': str(uuid4()), 'category': allowance['label'], 'quantity': 1,
                               'unit': 'allowance', 'unit_cost': float(allowance['amount']), 'basis': 'allowance',
                               'source': 'Reviewer allowance; not a contractor price', 'notes': allowance['note']})
    # Existing global reserve is preserved and applied once by the existing service.
    scope['notes'] = (scope.get('notes', '') + '\nQuote review: ' + option['label'] + '. ' + document.get('contingency_review_note', '')).strip()
    validated = RehabScope.model_validate(scope)  # Enforce existing limits; never truncate evidence.
    draft = copy.deepcopy(baseline['draft_input'])
    draft['rehab_budget'] = {'value': scope_total(validated), 'confidence': 'HIGH', 'source': 'reviewed scope total'}
    body = SaveDealRequest(address=baseline.get('address'), draft_input=draft, analysis_result={},
                           rehab_scope=validated, parent_deal_id=baseline['id'],
                           revision_note='Reviewed quote option: ' + option['label'])
    body.analysis_result = canonical_revision(body)
    return body.model_dump(mode='json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_json')
    parser.add_argument('--select', help='Prepare a local revision payload; does not send or save it.')
    args = parser.parse_args()
    with open(args.input_json) as source:
        document = json.load(source)
    output = {'status': 'EXPERIMENT ONLY', 'fixture_origin': document.get('fixture_origin', 'user supplied'),
              'comparison': compare(document)}
    if args.select:
        output['proposed_revision'] = revision_payload(document, args.select)
    print(json.dumps(output, default=str, indent=2))
