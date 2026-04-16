# test and maintain #1 

**main purpose**: 
- testing the flow of every single path in the system from users point of view.
- checking the correctness of numbers.
- checking for any bugs or errors.
- writing down the needed changes for each flow , and the layout improvements 


**target**: 
- the Whole system from login page to (owner/manager/cashier) interfaces 

/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\
\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/

## 1. login page and owner account first creation
 
- i need to make sure that the owner account username should be all in english , and the system should reject and arabic charachters or numbers or symobls **checked**

- i need to make sure that roles can only log to their sections or to a lower role section **checked**

- i need to make sure that the system is safe that there are no bugs that can result unauthenticated logging **checked**

===============================================================================================

*report*: 

- validations are mostly correct , tho we can replace the default browser error message with our own just like we did with "password confirming doesn't match" exception 

- user name validation are correct , blocks foregin letters 

- password validation *are missing* forgein letter blockers 

- password checking is going in the right direction 

- owner could login to lower roles section (correct)

- logging in and out has a *bad visual bug* , and that's successful login or logout messages don't disappear , a good solution is to give them a disappearing timer , 4 seconds seems enough  

===============================================================================================

/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\
\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/

## 2. owner UI and actions

### 1. accounts creation:

- create a new manager account **checked**
- create a new cashier account **checked**
- try creating a manager account that matches an existing account **checked**
- try to create a cashier account that matches and existing account **checked**
- try to create a manager account that matches an existing cashier account and the oppisite **checked**

### 2. account editing and deletions

- try editing an account's password then log in with it (and check if the old one still works)**checked**
- try editing an account's username then log in with it (and check if the old one still works)**checked**
- check the time and date of an account creation **checked**

### 3. editing the owner account 

- editing user name and see if works **checked**
- see if the old password check is validating correctly **checked**
- editing password and see if works **checked**
- trying to enter a new password with a different confirming password **checked**

### 4. testing the filters and the correctness of the audit log page `delayed`

- ####### 

===============================================================================================

*report*:

- owner UI first impression is *ugly* that is because of the empty main menu page, could fill it with and image , or some professional propriote statistics , we can start with an image for now

- owner account edit : successfully able to change the owner password with keeping the same username , couldn't log in with old password , could log in with new password , after changing password the system is taking me to the login page (which is great)

- owner account edit : successfully able to change owner username with keeping same password
- *tho a bad bug in authentication* in owner account change is that it's auto filling the current user name , which is bad security bug , we should not fill this up

- owner account edit : sucessfully able to change both username and password at the same time

- *uselss* roles button in lower menu of account management , should be deleted

- successfully created a manager account with right validations steps(username:fefe,password:fefefefe)

- could not create another manager/cashier account with same existing username:fefe , tho i could reuse an existing password (good)

- could not create a new account with same owner user name (good)

- could change a manager's account username and log in with it , could not log in with old username (good)

- could change a manager's account password and log in with it (good)

- could change a manager's account username and password at the same time (good)

- *bad visual* : successful updating , deleting , adding messages are not disappearing , should be handled like the login messages

- could delete an account smoothly , could not log in with a deleted account (good)

- *lack of info* : owner can't see other accounts passwords , tho owner should be able to , maybe in edit form we can insert the old password for the selected account

- *bad visuals* of the 3 pages (edit owner account , accounts list , account creation) , should be enhanced

- *lack in info* , there should be records for accounts edits and deletions , creation is kinda getting recorded and being shown in accounts list page

===============================================================================================


/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\
\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/

## 3. manager UI and actions

### 1. catalog

- test creating a collection 
- test creating a product 
- test wrong product inputs
- test father sets creation 
- test searching bar 
- test lower navigation menu pages by creating more then 20 collection
- test reusing names , id , codes , barcodes , 

### 2. billing
 
### 3. financials

### 4. stock

### 5. 

===============================================================================================

*report*: 

- we have *same issue* with empty main page screen , can be filled with a background initially then some statistics about bills and stuff later

- also we can highligh each button the user is in it's page to make the user keep a track of which page they are in 

1) **catalog** : 

- for products search panel , *we can add* an (no products with this name were found) to the recommendation list as a way to let the user know that the search is working but input is either wrong or invalid

- messages such as (collection was created , product was added) *should as well* have a disappearing timer of 4 secs

- collection existing name validation while creating a new collection is working (great)

- when the add a collection button is clicked , the system should auto start excepting input in the textbox

- when searching for a collection that is in another page then first page , the system is highlighting it's element without actually taking the user to it's page , which is a *big issue* and makes search uneffecient 

- for some reason excel import/export button in upper menu is always highlighted while i am in the products management page *which is a visual bug*

- the word "زمر" above the collections table is placed in a fucked up place *gotta fix that*

- the collection / father sets / products pages are having different heights depending on how many elements they contain , which is *a kind of an issue* , we should keep the same height for the right panel , which will also make the pages navigation bottom section maintain the same place in the page no matter how many elements there were in the page

- adding new product form is currently *so fucked up* visually , gotta fix that 

- recomendation list for search panel in search by name is only displaying 3 elements , it should display up to 20 , but not all at once , a good way to handle it is to make that the recomendation list height shows only 3 elements , but give it a scroll bar that users can navigate up and down through elements *which is important for better search experience*
- same thing for product adding form in the collections textbox recomendation list

- product adding form , selecting same second unit as first should let the system know that this product only has one unit of measurement , and make the system auto depend on first unit of measurement in this case always *which is very important* , and also in this case ( same second unit as first ) must disable the ability to enter a conversion factor 

- for saling and purchasing currencies permissions in the adding a product form , instead of having three option (auto:SYP , SYP , dollars) we can have only 2 : (auto:SYP , dollars) and the system is always set to auto:SYP at first

- user currently can delete the first textbox for (1st code , 2nd code , 1st barcode , 2nd barcode) , *tho it's not apropiate* , we should prevent user from deleting the first textbox of all of 4

- setting a default cost , price for a currency while adding a new product is auto filling the latest cost , price in the edit form , which is wrong, this value *needs to be only* updated when a purchase bills , sale bill happen that contained the product

- codes and barcodes that got saved when creating a new product are getting shown in their textboxes in edit page *which is a big issue*

- collection name changing is working fine 

- we need to *delete the useless* import excel button next to collection deletion and next to father set deletions buttons , cause we already created it's own page in upper menu

- we *must* delete the whole logic of collection / father sets cost / price changing , we will add this option in tools *later* 

- the path text in edit product form is only showing : collectionName/fatherSetName/ , it should show the selected product name *and that's for better tracking*

- *we must figure out* how to handle product deletions , fathersets deletions and collections deletions the right way , deletions should not affect old records if they contained the deleted product/fatherset/collection , also deletions should not prevent creating a new product with a properity the existed in the deleted product (name , code , barcode ...etc) , we need to discuss it as we reach this step 

- an *important* feature we can add to product edit and addition page, is letting the manager allow discounts or not in sales , and if allowed let the manager set a limit or keep the discount up to the cashier 

- also *kinda important* we can give the manager the option to set a price / cost for second unit that is different from the price / cost of first unit x conversion factor , or the manager can let the system auto set the cost / price of the 2nd unit as it does now

- the id that the system will auto give to a product is not appearing in the adding new product form , it's only appearing in edit form *which is kind of a problem*

- add a method for user to make the system auto fill SYP / dolllars cost / price according to current FX when the user enters one of them *should exist* cause it will make things much easier


2) **excel import** : 

- we can move the excel import button from being in upper menu of the manager UI , to be in lower menu of products management *which is a better place*
- the layout of the excel import first phase page looks so initial and raw , should be fixed into something better *right now it's visually garbage*
- the excel import should allow multiple collections importing 
- the second phase page looks kinda better , tho it can be more like an excel sheet using some css , that will make it more comfortable to work with for the users 
- new fields should be added to second phase columns : (collection name) , and instead of cost and price , we need to have default cost in SYP , default cost in dollars , default price in SYP , default price in dollars 
- third phase UI is actually good , can be kept , tho we can use highlights and order rows to keep the user in track of events : priority#1 : error rows , preiority#2 : edited rows with no errors , priority#3 : rows with no errors since they were added , in all 3 rows should be decending according to arabic alphabet for names , english names can be added at either top or bottm with also alphabet order for their rows 
- i couldn't test importing an excel file now cause some changes must be applied first such as make the import support the new logic of multiple currencies `delayed`

3) **excel import** : 

- same as excel imports , tho the whole structure of steps can be enhanced further , first phase is kinda messy 
- for some reason , while in excel export page , the logout button css changes , and the whole css is different from the other system pages , we can use the export css with the import excel page , tho the logout button should keep the same css 


4) **billing** : 

- i noticed that while i am in the billing management page the log out button is *pure white* which is a css bug that *needs to be fixed*

`providers profiles page`

- we need to handle provider profiles deletions the right way that doesn't blow up the entire system *so important*
- i also noticed that debts button for a provider row in providers profiles page is disable , we need to *re enable it* , it should take the user to the right debts page with the provider name / or id in the filters 
- i don't think that show only active/ show all options are necessary in the page *we can delete the option and make the page always show all*
- there is no way to show a provider notes , we might want to create a view provider page that shows many details for a single provider in the feature including some statistics *important*

`purchase bills creation page` 

- page layout is kinda good , except the fact that since we added currencies expanded section the users now need to scroll up and down through the page in order to see all section , we need to fix that , *i need all the page elements to be contained in one screen size*
- we can also have some shortcuts to auto switch between section (we can do them similary to POS shortcuts ) 
- we need to translate english stuff in purchase bills page to arabic 
- 

5) **financials** : 

- the lower menu buttons of financials page have wierd ass names *gotta fix that*
- when creating a new money container , by default here are the options that need to be filled out before user change them : (all tasks : checked , active : unchecked , both currencies enabled , allowed users : all checked)
- creating a money container page layout is actually good , can be kept as is
- not selecting any of the account in accessable section of creating a new container should prevent all accounts from using this money container instead of enabling all
- notes box in mc creation can be expanded infinitly and fucks up the page layout , *it should be limited*
- the show movements button in financials page in moneycontainers list is disabled , we need to enable it , it's supposed to *take the user* to movements page with the money container name / id in the filters 


===============================================================================================



/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\
\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/


## 4. cashier UI and actions


/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\
\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/