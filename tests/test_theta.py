"""Quick smoke-test: fetch theta for a known contract and print the result."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.market_data_service import fetch_option_theta

result = fetch_option_theta('SPY', '2026-05-15', 570, 'CALL')
print('theta:', result)
