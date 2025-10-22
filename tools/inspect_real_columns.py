#!/usr/bin/env python3
"""检查 Alibaba 2018 trace 真实的列结构"""
import pandas as pd
import sys

# 读取前 5 行，不指定列名
print("━━━ 查看前 5 行原始数据 ━━━\n")
df_raw = pd.read_csv(f"{sys.argv[1]}/batch_instance.csv", nrows=5, header=None)

print(f"总列数: {len(df_raw.columns)}\n")
print("前 5 行数据:")
print(df_raw)

print("\n━━━ 各列数据类型 ━━━")
for i, col in enumerate(df_raw.columns):
    sample = df_raw[col].iloc[0]
    print(f"列 {i}: {sample} (type: {type(sample).__name__})")

# 尝试读取更多行，统计数值列
print("\n━━━ 读取 1000 行，查找数值列 ━━━")
df_1k = pd.read_csv(f"{sys.argv[1]}/batch_instance.csv", nrows=1000, header=None)

for i in range(len(df_1k.columns)):
    try:
        numeric = pd.to_numeric(df_1k[i], errors='coerce')
        non_nan_count = numeric.notna().sum()
        if non_nan_count > 0:
            print(f"列 {i}: {non_nan_count}/1000 是数值")
            print(f"  范围: {numeric.min():.2f} - {numeric.max():.2f}")
            print(f"  均值: {numeric.mean():.2f}")
    except:
        pass

