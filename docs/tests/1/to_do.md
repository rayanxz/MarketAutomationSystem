
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

- "no product matches the entered name" element in recommendation list of the search panel on the left
- give messages (collection was created successfully , product was ....etc) a disappearing timer of 4 secs
- make it that if the add a collection button is clicked the textbox auto starts expecting input , no need for clicking it 
- searching for a collection/father set / product that is not in the first page in the pages panel should make the system auto take the user to the page that contains the element the user is looking for , highlighting is currently working great btw
- fix the highlighted excel import/export button in upper menu while i am in products management page


- make the collection / father sets / products right panel maintain the same height (does not change height depending on how many rows it has) , and that height should be the same height as when it contains maximum amount of rows (15 rows) , and also make the pages navigation bottom section in the right panel maintain the same place in the page
- fix the layout of the adding new product form *visually*
- make the recomendation list for search by name in search panel display up to 20 rows , but give it the height of 3 rows , and give it a scroll bar so user can navigate up and down through elements
- let the system know that selecting same second unit as first in product adding form means that the product only has one unit of measurement , and which makes the system always depend on the first unit and disable the ability to enter a conversion factor in the form
- remove the SYP radio button from the product adding form , keep the auto:SYP and dollars 
- we should prevent user from deleting the first textbox of all of 4 (1st code , 1st barcode , 2nd code , 2nd barcode) textboxes in the adding product form
- fix the updated latest cost , price in a product edit form when the default value is set
- fix the codes and barcodes are not gettings saved and not shown in their textboxes in the edit page of a product 
- remove the useless import excel button next to collection / father set deletions buttons 
- remove the logic of collection / father set all products cost / prices changing 
- show the name of the product the user is editing in the path text up there
- handle the current issue of deletions in the right way that satisfies a good accounting system
- a feature we can add to product edit and addition page, is letting the manager allow discounts or not in sales , and if allowed let the manager set a limit or keep the discount up to the cashier 
- we can give the manager the option to set a price / cost for second unit that is different from the price / cost of first unit x conversion factor , or the manager can let the system auto set the cost / price of the 2nd unit as it does now
- show the id that the system is going to give to a product in the adding form 
- add a method to both product addint form and editing form that makes the system auto fill either SYP or dollars cost/price according to current FX and user input to the other one 


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

- fix the names of lower menu buttons
- do some changes on by default filled options while creating a new money container before user edit
- all accounts disabled in accessable section of mc creation => no accounts can use it until this fact is changed
- limit the expanding of notes textbox in mc creation page
- enable the show movements button in money containers list page
- 