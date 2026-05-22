from services.market_data_service import fetch_option_theta
result = fetch_option_theta('SPY', '2026-05-15', 570, 'CALL')
print('theta:', result)
