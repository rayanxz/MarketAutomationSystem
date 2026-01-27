# whole bills , returns , stock , transactions, financials , debts , POS and more test scenarios

**note** : this whole test will focus only on logic , UI notes will be written during the test scenario but won't be mentioned in this file , 

`catalog part`:

1) create up to 10 products in catalog (testing product 1 , testing product 2 ,.etc) *checked, created 5*
2) give each one a cost that matches it's number x10 and a price that matches it's number x 100 (testing product 1 : cost=10,price=100) for SYP *checked*
3) give each one a cost matches it's number and a price that matches it's number x 10 (testing product 3 : cost=3,price=30) for dollars *checked*
**we will ignore codes  , barcodes usage for now till we fix them in catalog , tho we should consider ids since they are supposed to be currently working**
4) give each product different units of measurements and conversion factors *checked*

*the list of products created during the test flow:*
=============================================================

|id |product name|father set|1st unit|2nd unit|cf|SYP sale|SYP pur|$sale|$pur|SYPc|SYPp|$c|$p|
|003|      tp1   |    tfs1  | item   |  pck   |10|   TD   |   TD  |  T  |  T | 10 | 100| 1|10|
|004|      tp2   |    tfs1  | gram   |  pck   |20|    F   |   F   |  T  |  T | 20 | 200| 2|20|
|005|      tp3   |    tfs1  | gram   |  item  |30|    T   |   T   |  F  |  F | 30 | 300| 3|30|
|006|      tp4   |    tfs2  | pckg   |  box   |40|    F   |   T   |  T  |  F | 40 | 400| 4|40|
|007|      tp5   |    tfs2  | item   |  pck   |50|    T   |   T   |  TD | TD | 50 | 500| 5|50|

=============================================================

`providers side`: 

- i will create a provider called "tester" *checked*

`financials side`: 

- i will create a money container called tmc1 , and keep it fully enabled for all actions and users *checked*
- initial value for tmc1 => 1000 SYP , 100 dollars *checked*
- set the FX value to 10,000 SYP = 1 $ *checked*

## track #1 , purchase bill and it's effect : 

`billing part`: 

1) from `create purchase bill` page , i will create a bill with following info : 
2) add all testing products at once without changing any of there propereties , and each one will have a quantity of 1 
3) select "tester" provider , and "tmc1" money container
4) check the full cost of the bill , and keep the paymenet method full payment 
5) save the bill 

*track #1 effect* on : 


