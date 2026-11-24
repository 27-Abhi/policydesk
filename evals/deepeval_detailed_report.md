# PolicyDesk DeepEval Detailed Evaluation Report

- **Timestamp**: `2026-10-26T08:48:35Z`
- **Evaluation Framework**: `DeepEval v4.3.1`
- **LLM-as-a-Judge Model**: `gemini-3.1-flash-lite`
- **Total Test Cases**: `10`
- **Overall Pass Rate**: **80.0%**
- **Average Faithfulness**: **100.0%**
- **Average Answer Relevancy**: **88.3%**

## Test Cases Summary Table

| ID | Category | Query | Faithfulness | Relevancy | Verdict |
|---|---|---|---|---|---|
| q001 | account_types | What types of deposit accounts does the bank offer? | 1.00 | 1.00 | ✅ PASSED |
| q002 | account_types | What is a Basic Savings Bank Deposit Account and what are its features? | 1.00 | 1.00 | ✅ PASSED |
| q003 | account_types | What is the minimum deposit amount for a bulk term deposit? | 1.00 | 1.00 | ✅ PASSED |
| q004 | account_types | Can a current account earn interest? | 1.00 | 1.00 | ✅ PASSED |
| q005 | account_opening | Who can open a savings bank account? | 1.00 | 1.00 | ✅ PASSED |
| q006 | account_opening | What KYC documents are required to open a bank account? | 1.00 | 0.50 | ❌ FAILED |
| q007 | account_opening | Can I open a bank account online or do I need to visit the branch? | 1.00 | 1.00 | ✅ PASSED |
| q008 | account_opening | What entities are eligible to open a current account? | 1.00 | 0.33 | ❌ FAILED |
| q009 | account_maintenance | What happens if I fail to maintain the minimum balance in my savings account? | 1.00 | 1.00 | ✅ PASSED |
| q010 | service_charges | How far in advance will the bank notify me before changing service fees? | 1.00 | 1.00 | ✅ PASSED |


## Per-Question Deep Traces & LLM Explanations

### [q001] What types of deposit accounts does the bank offer?

- **Category**: `account_types`
- **Cosine Retrieval Score**: `0.5447`

**Retrieved Context Chunk**:
```
1.TYPES OF DEPOSIT ACCOUNTS 
While various deposit products offered by the Bank are assigned different names, the deposit products 
can be categorized broadly into the following types. Definition of major deposits schemes are as under: 
a. "Demand Deposits" means a deposit received by the Bank which is withdrawable on demand.

i) “Savings deposit” means a form of interest bearing demand deposit which is a deposit 
account whether designated as “Savings Account”, “Savings Bank Account”, “Savings 
Deposit Account”, “Basic Savings Bank Deposit Account (BSBDA)” or other account by 
whatever name called which is subject to the restrictions as to the number of withdrawals 
as also the amounts of withdrawals permitted by the bank during any specified period; 
Savings accounts opened for Non -Individual entities refers to a form of Demand deposit  
account maintained by entities other than individuals, wherein withdrawals are allowed 
any number of times depending up on the balance in the account.
```

**PolicyDesk Answer**:
> The bank offers "Demand Deposits," which include "Savings deposits" (such as Savings Accounts, Savings Bank Accounts, Savings Deposit Accounts, and Basic Savings Bank Deposit Accounts) and Savings accounts for Non-Individual entities.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the response is perfectly aligned with the input, providing a clear and accurate overview of the bank's deposit account offerings. Great job!

---

### [q002] What is a Basic Savings Bank Deposit Account and what are its features?

- **Category**: `account_types`
- **Cosine Retrieval Score**: `0.4256`

**Retrieved Context Chunk**:
```
Details about the product 
offering are available with all the branches / website. (vii) Basic Savings Bank Deposit Account 
The Bank offers a BSBDA Account with all the basic banking facilities with Zero Initial Pay-In and Zero 
balance account. Details about the product offering are available with all the branches / website. (viii) Bulk Term Deposits  
 
Bulk Deposit” refers to Single  Rupee term deposits of ₹ 3 crores and above or such amount as 
amended by RBI from time to time.

3.KEY FEATURES ON OPERATION OF DIFFERENT TYPES OF ACCOUNTS  
(i) Operation of Joint Account 
a. The Joint Account opened by more than one individual can be operated by single individual or 
by more than one individual as either  or survivor, Former or Survivor, anyone or survivor or 
jointly. The mandate for operating the account can be modified with the consent of all account 
holders. b.
```

**PolicyDesk Answer**:
> A Basic Savings Bank Deposit Account (BSBDA) is an account offered by the Bank that provides all basic banking facilities. Its features include a Zero Initial Pay-In and a Zero balance requirement. Further details regarding the product offering are available at all branches or on the website.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the response provided a comprehensive and accurate explanation of a Basic Savings Bank Deposit Account and its features without including any extraneous or irrelevant information. Great job!

---

### [q003] What is the minimum deposit amount for a bulk term deposit?

- **Category**: `account_types`
- **Cosine Retrieval Score**: `0.4793`

**Retrieved Context Chunk**:
```
Details about the product 
offering are available with all the branches / website. (vii) Basic Savings Bank Deposit Account 
The Bank offers a BSBDA Account with all the basic banking facilities with Zero Initial Pay-In and Zero 
balance account. Details about the product offering are available with all the branches / website. (viii) Bulk Term Deposits  
 
Bulk Deposit” refers to Single  Rupee term deposits of ₹ 3 crores and above or such amount as 
amended by RBI from time to time.

3.KEY FEATURES ON OPERATION OF DIFFERENT TYPES OF ACCOUNTS  
(i) Operation of Joint Account 
a. The Joint Account opened by more than one individual can be operated by single individual or 
by more than one individual as either  or survivor, Former or Survivor, anyone or survivor or 
jointly. The mandate for operating the account can be modified with the consent of all account 
holders. b.
```

**PolicyDesk Answer**:
> The minimum deposit amount for a bulk term deposit is ₹3 crores or such amount as amended by the RBI from time to time.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the response is perfectly accurate and directly addresses the inquiry without any extraneous information. Great job providing a clear and concise answer!

---

### [q004] Can a current account earn interest?

- **Category**: `account_types`
- **Cosine Retrieval Score**: `0.3321`

**Retrieved Context Chunk**:
```
This account earn 
interest and are intended for eligible organisations permitted to open such accounts 
under RBI Master direction on interest rates on deposit. ii) "Current Account" means a form of non -interest-bearing demand deposit wherefrom 
withdrawals are allowed any number of times depending upon the balance in the account 
or up to a particular agreed amount and will also include other deposit accounts which 
are neither Savings Deposit nor Term Deposit.

4 
Comprehensive Deposit Policy June 2025 
b. "Term Deposits" means an interest-bearing deposit received by the Bank for a fixed period 
and shall also include deposits such as Recurring /Reinvestment deposits. 2. ACCOUNT OPENING AND OPERATION OF DEPOSIT ACCOUNTS  
(i)Account Opening and Operation of Various Deposit Accounts 
a.
```

**PolicyDesk Answer**:
> No. According to the policy, a "Current Account" is defined as a form of non-interest-bearing demand deposit.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the response is perfectly accurate and directly addresses the inquiry without any extraneous information. Great job providing a clear and concise answer!

---

### [q005] Who can open a savings bank account?

- **Category**: `account_opening`
- **Cosine Retrieval Score**: `0.5130`

**Retrieved Context Chunk**:
```
i) “Savings deposit” means a form of interest bearing demand deposit which is a deposit 
account whether designated as “Savings Account”, “Savings Bank Account”, “Savings 
Deposit Account”, “Basic Savings Bank Deposit Account (BSBDA)” or other account by 
whatever name called which is subject to the restrictions as to the number of withdrawals 
as also the amounts of withdrawals permitted by the bank during any specified period; 
Savings accounts opened for Non -Individual entities refers to a form of Demand deposit  
account maintained by entities other than individuals, wherein withdrawals are allowed 
any number of times depending up on the balance in the account.

This account earn 
interest and are intended for eligible organisations permitted to open such accounts 
under RBI Master direction on interest rates on deposit. ii) "Current Account" means a form of non -interest-bearing demand deposit wherefrom 
withdrawals are allowed any number of times depending upon the balance in the account 
or up to a particular agreed amount and will also include other deposit accounts which 
are neither Savings Deposit nor Term Deposit.
```

**PolicyDesk Answer**:
> Based on the provided policy, savings accounts can be opened by individuals and non-individual entities (eligible organizations permitted to open such accounts under RBI Master direction on interest rates on deposit).

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the response is perfectly accurate and directly addresses the inquiry with no extraneous information. Great job providing a clear and concise answer!

---

### [q006] What KYC documents are required to open a bank account?

- **Category**: `account_opening`
- **Cosine Retrieval Score**: `0.3517`

**Retrieved Context Chunk**:
```
The Bank before opening any deposit account will carry out due diligence as required under 
"Know Your Customer" (KYC) guidelines issued by RBI and such other norms or procedures 
adopted by the Bank. b. The account opening forms and other material would be made available to the prospective 
depositor by the Bank.

The same will contain details of information to be furnished and 
documents to be produced for verification and for record, it is expected of the Bank official 
opening the account, to explain the procedural formalities and provide necessary clarifications 
sought by the prospective depositor when he approaches opening a deposit account. Customers 
also have the option to open accounts digitally. c.
```

**PolicyDesk Answer**:
> The provided policy does not list specific KYC documents. It states that the Bank will carry out due diligence as required under RBI "Know Your Customer" (KYC) guidelines and that the account opening forms will contain details of the information to be furnished and the documents to be produced for verification.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `0.50` (Threshold >= 0.70)
*Judge Explanation*: The score is 0.50 because the response acknowledges the existence of KYC guidelines without providing the specific list of required documents requested by the user, focusing instead on procedural generalities.

---

### [q007] Can I open a bank account online or do I need to visit the branch?

- **Category**: `account_opening`
- **Cosine Retrieval Score**: `0.1407`

**Retrieved Context Chunk**:
```
The term deposit must run for a minimum tenor of 7 days from the date of value date in order to earn 
any interest. Premature withdrawals before this minimum tenor will result in zero interest payment to 
the customer. Sweep-in / Partial withdrawal may not be allowed for certain bulk deposits by bank. Deposits accounts transferred from one bank branch to another bank branch on account of takeover 
of bank branches in rural and semi-urban centers will adhere to the following conditions: 
a.

Deposit accounts will be deemed to be transferred to the new bank and will continue to be  
governed by the terms of contract agreed to, between the customer and the bank branch 
that is being taken over. b. The same rate of interest will be payable till maturity on such transferred deposits, as was 
payable at the time of takeover of the branch.
```

**PolicyDesk Answer**:
> The provided policy context does not contain information regarding whether you can open a bank account online or if you are required to visit a branch.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because your response was perfectly accurate, concise, and directly addressed the user's question without any unnecessary information. Great job!

---

### [q008] What entities are eligible to open a current account?

- **Category**: `account_opening`
- **Cosine Retrieval Score**: `0.2469`

**Retrieved Context Chunk**:
```
This account earn 
interest and are intended for eligible organisations permitted to open such accounts 
under RBI Master direction on interest rates on deposit. ii) "Current Account" means a form of non -interest-bearing demand deposit wherefrom 
withdrawals are allowed any number of times depending upon the balance in the account 
or up to a particular agreed amount and will also include other deposit accounts which 
are neither Savings Deposit nor Term Deposit.

4 
Comprehensive Deposit Policy June 2025 
b. "Term Deposits" means an interest-bearing deposit received by the Bank for a fixed period 
and shall also include deposits such as Recurring /Reinvestment deposits. 2. ACCOUNT OPENING AND OPERATION OF DEPOSIT ACCOUNTS  
(i)Account Opening and Operation of Various Deposit Accounts 
a.
```

**PolicyDesk Answer**:
> According to bank policy:

This account earn 
interest and are intended for eligible organisations permitted to open such accounts 
under RBI Master direction on interest rates on deposit. ii) "Current Account" means a form of non -interest-bearing demand deposit wherefrom 
withdrawals are allowed any number of times depending upon the balance in the account 
or up to a particular agreed amount and will also include other deposit accounts which 
are neither Savings Deposit nor Term Deposit.

4 
Comprehensive Deposit Policy June 2025 
b. "Term Deposits" means an interest-bearing deposit received by the Bank for a fixed period 
and shall also include deposits such as Recurring /Reinvestment deposits. 2. ACCOUNT OPENING AND OPERATION OF DEPOSIT ACCOUNTS  
(i)Account Opening and Operation of Various Deposit Accounts 
a.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `0.33` (Threshold >= 0.70)
*Judge Explanation*: The score is 0.33 because the output is heavily cluttered with irrelevant technical definitions, metadata, and descriptions of unrelated account types, which obscures the actual eligibility criteria requested.

---

### [q009] What happens if I fail to maintain the minimum balance in my savings account?

- **Category**: `account_maintenance`
- **Cosine Retrieval Score**: `0.5323`

**Retrieved Context Chunk**:
```
Any change in service & fees 
will be communicated one month in advance to all customers before effecting the change. All customer s will be  communicated regarding the balance requirement up -front at the time of 
opening an account with the bank. If the customer fails to maintain the required minimum balance in 
his/her savings account, the bank notifies the same to customer.

The communication states that in 
the event of the minimum balance not being restored in the account within the next 30 days from the 
date of notice, penal charges will be levied. In case the balance is restored within given notice period, 
no charges are levied, else the bank debits the applicable charges from the customer's account. AMB 
charges defined by the bank are directly proportionate to the extent of shortfall in the saving account.
```

**PolicyDesk Answer**:
> If you fail to maintain the required minimum balance in your savings account, the bank will notify you. You then have 30 days from the date of that notice to restore the balance.

*   **If you restore the balance within the 30-day notice period:** No charges will be levied.
*   **If you fail to restore the balance within the 30-day notice period:** The bank will debit applicable penal charges from your account. These charges are directly proportionate to the extent of the shortfall.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the response directly and accurately addresses the consequences of failing to maintain a minimum balance; great job providing a clear and helpful answer!

---

### [q010] How far in advance will the bank notify me before changing service fees?

- **Category**: `service_charges`
- **Cosine Retrieval Score**: `0.3973`

**Retrieved Context Chunk**:
```
Any change in service & fees 
will be communicated one month in advance to all customers before effecting the change. All customer s will be  communicated regarding the balance requirement up -front at the time of 
opening an account with the bank. If the customer fails to maintain the required minimum balance in 
his/her savings account, the bank notifies the same to customer.

The communication states that in 
the event of the minimum balance not being restored in the account within the next 30 days from the 
date of notice, penal charges will be levied. In case the balance is restored within given notice period, 
no charges are levied, else the bank debits the applicable charges from the customer's account. AMB 
charges defined by the bank are directly proportionate to the extent of shortfall in the saving account.
```

**PolicyDesk Answer**:
> The bank will communicate any change in service and fees one month in advance.

**DeepEval Faithfulness Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the actual output is perfectly aligned with the retrieval context—fantastic work!

**DeepEval Answer Relevancy Score**: `1.00` (Threshold >= 0.70)
*Judge Explanation*: The score is 1.00 because the response is perfectly accurate and directly addresses the inquiry without any extraneous information. Great job providing a clear and concise answer!

---

