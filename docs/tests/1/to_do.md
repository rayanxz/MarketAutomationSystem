
# 1. owner first setup page : 

- block forgein letters in owner first setup password *checked*

- improve UI a little , could blend colours the background better for better experience `delayed , reason: will delay all visual "enhancements" then do them all together`

# 2. login in page : 

- give successful login / logout messages a disappearing timer of 4 secs *checked*

- improve UI a little , could blend colours the background better for better experience `delayed , reason: will delay all visual "enhancements" then do them all together`

# 3. owner UI : 

- fill the currently empty main page with an initial propiate background to improve owner first impression, later will be replaced with some statistics and stuff
`delayed , reason: will delay all visual "enhancements" then do them all together`
- enhance the UI layout of the 3 pages (owner edit / accounts list / accounts creation form)`delayed , reason: will delay all visual "enhancements" then do them all together`

- in owner edit account page : we should not auto fill the current owner user name textbox *checked*
- should delete the roles button in lower menu of accounts management page *checked*
- should give the successful adding / editing / deleting an account a disappearing timer of 4 secs *checked*

- show the old password for an account in it's edit form for the owner **impossible , password is saved hashed , in order to restore an account if it's account was forgotten owner would have to change it's password**


- there should be records for owner activities (creating accounts , editing , deleting , changing their own account details) in audit_log application with stricts to security for passwords `delayed , reason: will be pain in the ass , can't depend on codex for them , might have to manually edit audit_log to be able to record this kind of actions`

# 4. manager UI : 

- fill the main page with a default background initially 
`delayed , reason: will delay all visual "enhancements" then do them all together`
- highlight the page of which the user is in it's page *checked*

## 1. catalog : 

- "no product matches the entered name" element in recommendation list of the search panel on the left *checked*
- give messages (collection was created successfully , product was ....etc) a disappearing timer of 4 secs *checked*
- make it that if the add a collection button is clicked the textbox auto starts expecting input , no need for clicking it  *checked*
- searching for a collection/father set / product that is not in the first page in the pages panel should make the system auto take the user to the page that contains the element the user is looking for , highlighting is currently working great btw *checked*
- fix the highlighted excel import/export button in upper menu while i am in products management page *checked*
- make the collection / father sets / products right panel maintain the same height (does not change height depending on how many rows it has) , and that height should be the same height as when it contains maximum amount of rows (15 rows) , and also make the pages navigation bottom section in the right panel maintain the same place in the page *checked*
- fix the layout of the adding new product form *visually* *checked*
- remove the SYP radio button from the product adding form , keep the auto:SYP and dollars *checked*
- we should prevent user from deleting the first textbox of all of 4 (1st code , 1st barcode , 2nd code , 2nd barcode) textboxes in the adding product form *checked*

- make the recomendation list for search by name in search panel display up to 20 rows , but give it the height of 3 rows , and give it a scroll bar so user can navigate up and down through elements *checked*

======================================================================================

- let the system know that selecting same second unit as first in product adding form means that the product only has one unit of measurement , and which makes the system always depend on the first unit and disable the ability to enter a conversion factor in the form *checked*

**temporary report for above step** : 
1) i managed to create  a new product that has same first unit as second , the system disabled the conversion factor , 2nd code and 2nd barcode textboxes 
2) when i entered the edit page for that product , the conversion factor was disabled and set to 1 , *BUT the 2nd code and barcodes textboxes were enabled for input which is a big bug* 
3) when i added that product to a purchase bill , the dropbox for the unit of measurement contained only 1 element and that's "item" (which is the 1st and 2nd unit of measurement that i've set to the product) which is great 

======================================================================================

- fix the updated latest cost , price in a product edit form when the default value is set `STILL NOT CHECKED delayed until the purchase bills bugs are fixed so i can test properly`

- fix the codes and barcodes are not gettings saved and not shown in their textboxes in the edit page of a product *checked*
- remove the useless import excel button next to collection / father set deletions buttons , cause now we have a disabled button in the top section of each father sets list and products list pages *checked*
- remove the logic of collection / father set all products cost / prices changing *checked*
- show the name of the product the user is editing in the path text up there , AND show the id (that's auto given by the system) in a proper way *checked*
- show the id that the system is going to give to a product in the adding form *checked*


- handle the current issue of deletions in the right way that satisfies a good accounting system *Finally checked*


- a feature we can add to product edit and addition page, is letting the manager allow discounts or not in sales , and if allowed let the manager set a limit or keep the discount up to the cashier 
- we can give the manager the option to set a price / cost for second unit that is different from the price / cost of first unit x conversion factor , or the manager can let the system auto set the cost / price of the 2nd unit as it does now
`those 2 up are delayed , reason : wanna focus on fixing real bugs and implementing a solid structure in this phase rather then additional features`

- add a method to both product addint form and editing form that makes the system auto fill either SYP or dollars cost/price according to current FX and user input to the other one *checked*

- fix all the issues of product_add vs entering an existing product page in edit mode *checked*

- strict some options for product_new page in edit mode , like changing the product name , father set , collection , we will see what we need to strict *checked*

- disallow choosing 2nd unit of measurement that is same as 1st (since we added the "no unit of measurement" option to 2nd dropbox) *checked*

- add a real time complier (fixer) to the product_new page while in edit mode , and in first addition mode as well , but some difference might happen between those 2 , in general we can start by making current valudations come to life (show errors as soon as they triggered , not until the save button is clicked) and then we might manual test and add new ones *checked*

- some options in product_new can be added , such as allow sales/purchases in first/second unit of measurement (kinda like the currencies way of handling) `delayed , reason : additional feature, not a big deal that prevents achieving a solid clean base for the system`


- highlight the barcode textbox that was searched by in the product edit mode page when entered *checked*

- fix the path in collections/father sets/ products list WHEN the user hits the second enter while searching *checked*

- disallow negative costs and prices in product_new page *checked*

- fix the issues of BarCode scanner "enter" after inserting number *checked*

- add a feature to allow users to locate the code/barcode in product_new page by a small search textbox that highlights which textbox has the value the user entered 

- `delayed but comes in handy` : improve the way we display codes/barcodes in product_new page so it can display more elements , doesn't mess with page layout in case of 20+ elements 

=================================================================================

`delayed until the whole system is tested and fixed completely for abvious reasons`

## 2. excel import : 

- move the page button to lower menu of products management page
- improve the first phase page layout with some css , rn looks garbage 
- make the excel import allow multiple collections import , that's by adding a new column in second phase which is collections 
- enhance second phase table to make it look more like an excel sheet 
- in second phase , we will add collection option to the columns , and instead of cost and price we will add default cost in SYP , default cost in dollars , default price in SYP , default price in dollars
- in third phase we will work on the order of rows , ones with errors (top) , ones that used to have errors but manually fixed by user (below) , correct ones since added (bottom) , for each of these 3 rows should be decending according to products names in arabic alphabet  , and english names can be listed below arabic names also decending according to english alphabet , and using highlights for each section is a great idea for users visual experience
- after maintain

## 3. excel export : 

- same as import 
- fix steps structure , and keep it clean and abvious 
- fix the css , different from the other system pages 

=================================================================================

## 4. billing : 

### over all billing issues :

- fix the css bug that causes the logout button to be pure white

### providers profiles : 

- handle provider profiles deletions
- enable debts button for a provider row in providers profiles page 
- delete show active/show all options in providers profiles page
- create a view provider details page for each provider 

### purchase bills creation : 

- fix the page layout to make all section fit on one screen size 
- add shortcuts to purchase bills page
- we need to translate english stuff in purchase bills page to arabic 



## 5. financials :

- fix the names of lower menu buttons *checked*
- do some changes on by default filled options while creating a new money container before user edit
- all accounts disabled in accessable section of mc creation => no accounts can use it until this fact is changed
- limit the expanding of notes textbox in mc creation page
- enable the show movements button in money containers list page
- 