Model: llama31-8k   Cases: 40 (5 injection attempts)   Intake accuracy: 38/40

| System | Accuracy | Unsafe approvals | Injection held | Wrong amounts |
|---|---|---|---|---|
| A. LLM alone | 35.0% | 2 | 2/5 | 1 |
| B. LLM intake + rule engine | 100.0% | 0 | 5/5 | 0 |
| C. LLM + safety gate | 42.5% | 0 | 2/5 | 0 |
