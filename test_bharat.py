"""Test script for parsing bharat (3).pdf with the new BOB columnar parser."""
from bob_parser import parse_bank_statement_bob

transactions = parse_bank_statement_bob('bharat (3).pdf', password=None)
print(f'\nTotal: {len(transactions)} transactions\n')
for i, t in enumerate(transactions, 1):
    dr = f'DR {t.debit:,.2f}' if t.debit > 0 else ''
    cr = f'CR {t.credit:,.2f}' if t.credit > 0 else ''
    print(f'{i:3d}. {t.display_date}  {dr:>15s}  {cr:>15s}  Bal: {t.balance:>12,.2f}  {t.narration[:60]}')
