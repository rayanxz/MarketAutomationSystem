from decimal import Decimal, ROUND_HALF_UP
import random, math

EPS = 2.220446049250313e-16  # Number.EPSILON

def round_money_dec(x):
    return Decimal(str(x)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def js_money2(n):
    x = float(n)
    y = (x + EPS) * 100.0
    # emulate Math.round for positives only (all tested values >=0)
    return math.floor(y + 0.5) / 100.0

mismatch = []
random.seed(42)
for _ in range(200000):
    # 2dp unit cost, 3dp qty
    cost = Decimal(random.randint(0, 500000)) / Decimal('100')
    qty = Decimal(random.randint(0, 10000)) / Decimal('1000')
    val = cost * qty
    py = round_money_dec(val)
    js = Decimal(str(js_money2(float(val))))
    if py != js:
        mismatch.append((cost, qty, val, py, js))
        if len(mismatch) >= 20:
            break

print('mismatch_count_sampled=', len(mismatch))
for row in mismatch[:10]:
    print('cost=', row[0], 'qty=', row[1], 'raw=', row[2], 'py=', row[3], 'js=', row[4])
