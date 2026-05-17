import pandas as pd
import numpy as np
import statsmodels.tsa.stattools as st

# --- 1. Generate Sample Time Series Data ---
# Create a sample time series that resembles a random walk
np.random.seed(42)
n_points = 100
time = np.arange(n_points)
# Create a series with some underlying trend and noise
data = 100 + 0.5 * time + np.random.normal(0, 5, n_points)
ts = pd.Series(data, index=time, name='Time Series Data')

print("--- Sample Time Series Data (First 10 points) ---")
print(ts.head(10))
print("\\n" + "="*50 + "\\n")

# --- 2. Calculate Autocorrelation Function (ACF) ---
# ACF measures the correlation between a time series and a lagged version of itself.
# A high correlation at lag k suggests a dependency between the value at time t and the value at time t-k.
acf_values = st.acf(ts.values, nlags=20, stationarise=False)
lags = range(1, len(ts))
acf_df = pd.DataFrame({
    'Lag': lags,
    'ACF Value': acf_values[1:]
})
print("--- Autocorrelation Function (ACF) ---")
print(acf_df)
print("\\n" + "="*50 + "\\n")

# --- 3. Calculate Moving Average (MA) ---
# MA smooths out the time series by averaging the values over a window.
# We will use a window of 5 for demonstration.
window_size = 5
ts_ma = ts.rolling(window=window_size).mean()

print(f"--- Moving Average (MA) with Window Size = {window_size} ---")
print(ts_ma.head(10))
print("\\n" + "="*50 + "\\n")

# --- 4. Intuitive Interpretation ---
print("--- Intuitive Explanation ---")
print("1. Autocorrelation (ACF):")
print("   - It tells us how much the series is correlated with its own past values (lags).")
print("   - If ACF is high at lag 1, it means today's value is highly dependent on yesterday's value.")
print("   - In a purely random series, ACF values for all lags (after lag 0) will be close to zero.")
print("2. Moving Average (MA):")
print("   - It smooths out the short-term fluctuations (noise) to reveal the underlying trend.")
print("   - It's a lagging indicator; it reflects the average over the last 'window_size' periods.")
print("   - The MA line is smoother than the original data, making trends easier to spot.")