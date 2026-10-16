Model: llama31-8k   Cases: 86 (12 injection attempts)   Intake accuracy: 83/86

| System | Accuracy | Unsafe approvals | Injection held | Wrong amounts |
|---|---|---|---|---|
| A. LLM alone | 47.7% | 4 | 9/12 | 0 |
| B. LLM intake + rule engine | 97.7% | 0 | 11/12 | 0 |
| C. LLM + safety gate | 52.3% | 0 | 9/12 | 0 |
