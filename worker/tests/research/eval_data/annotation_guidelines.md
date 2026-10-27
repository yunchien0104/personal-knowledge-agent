# Claim Verification Annotation Guidelines v1

## 評估目標

判斷提供的 Evidence 是否足以支持 Claim，而不是判斷 Claim 在真實世界中是否正確。

### 1. supported

證據直接支持 Claim，或透過合理、可追溯的推理足以支持 Claim。

例如：證據顯示 2025 年成長率 20%、2026 年預估 12%，因此「預估成長率下降 8 個百分點」屬於 supported。

### 2. contradicted

證據與 Claim 存在直接、明確且可比較的矛盾。

例如：證據顯示同一家公司、同一年度、同一指標的成長率為 20%，Claim 卻主張 35%。

### 3. insufficient_evidence

證據與 Claim 的主題相關，但不足以確立完整主張，也沒有明確反駁它。

例如：證據顯示成長率下降，但無法證明公司即將破產。

### 4. unsupported

提供的證據與 Claim 缺乏實質支持關係，例如完全無關，或只有無法構成合理推論的表面關聯。

例如：證據討論某公司的辦公室搬遷，Claim 卻主張另一家公司的毛利率上升。

## 標註優先順序

1. 有直接且明確的矛盾 → contradicted
2. 證據足以支持完整 Claim → supported
3. 證據相關但不足 → insufficient_evidence
4. 證據無實質支持關係 → unsupported

## 特別注意

- inference 必須檢查推理是否有效。
- hypothesis 不能只因為用了「可能」就判定 supported。
- 同一句 Claim 包含多項主張時，不能只驗證其中一部分就判為 supported。
- 資料的年度、單位、估計值與實際值必須保持一致。
- 有分歧的案例應標記待審，不應直接用來衡量模型準確率。
