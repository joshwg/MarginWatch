import numpy as np
import pandas as pd

# 1. Input data parsed directly from image_ce749e.png
# Net gains/losses mapped directly to the prior week's starting capital base
data = {
    'Week_Ending': [
        '1/4', '1/11', '1/18', '1/25', '2/1', '2/8', '2/15', '2/22', '3/1', '3/8', 
        '3/15', '3/22', '3/29', '4/5', '4/12', '4/19', '4/26', '5/3', '5/10', '5/17', 
        '5/24', '5/31', '6/7'
    ],
    'Prior_Value': [
        246857, 251341, 257523, 266605, 261195, 251074, 231078, 230462, 234608, 237229,
        236657, 245967, 243573, 228457, 235922, 236979, 262339, 267257, 269261, 241654,
        238114, 253644, 271547
    ],
    'Gain_Loss': [
        4484, 6182, 9082, -5410, -10121, -19996, -616, 4146, 2621, -572,
        9310, -2394, -15116, 7465, 1057, 25414, 4864, 2004, 4393, -3540,
        15530, 17903, -23036
    ]
}

df = pd.DataFrame(data)

# 2. Calculate true weekly returns
df['Weekly_Return'] = df['Gain_Loss'] / df['Prior_Value']

# 3. Define Risk-Free Rate benchmarks (Assuming 4.5% Annualized)
rf_annual = 0.045
rf_weekly = rf_annual / 52

# 4. Calculate Excess Returns
df['Excess_Return'] = df['Weekly_Return'] - rf_weekly

# --- Sharpe Ratio Calculation ---
mean_excess = df['Excess_Return'].mean()
std_excess = df['Excess_Return'].std(ddof=1)
sharpe_annual = (mean_excess / std_excess) * np.sqrt(52) if std_excess != 0 else 0

# --- Sortino Ratio Calculation ---
# Isolate downside deviation relative to the weekly risk-free benchmark
df['Downside_Return'] = df['Weekly_Return'].clip(upper=rf_weekly) - rf_weekly
downside_std = np.sqrt(np.mean(df['Downside_Return']**2))
sortino_annual = (mean_excess / downside_std) * np.sqrt(52) if downside_std != 0 else 0

print(f"Analysis Results (23-Week Performance Data):")
print(f"--------------------------------------------")
print(f"Annualized Sharpe Ratio  : {sharpe_annual:.2f}")
print(f"Annualized Sortino Ratio : {sortino_annual:.2f}")

