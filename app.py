from flask import flash, Flask, render_template, redirect, url_for, session, request, Markup
from flask_mysqldb import MySQL
import yaml
from flask_mail import Mail
import smtplib
import ssl
import re
from functions.dbConfig import database_config
from authlib.integrations.flask_client import OAuth
import os
from datetime import timedelta
from datetime import date


app = Flask(__name__)

env = "dev" if os.environ.get("ENV") != "PROD" else ""
DATABASE_URL = ""
if env == "dev":
    dev = yaml.load(open('db.yaml'), Loader=yaml.FullLoader)
    DATABASE_URL = dev['CLEARDB_DATABASE_URL']

else:
    DATABASE_URL = os.environ.get("CLEARDB_DATABASE_URL")

user, password, host, db = database_config(DATABASE_URL)

app.config['MYSQL_HOST'] = host
app.config['MYSQL_USER'] = user
app.config['MYSQL_PASSWORD'] = password
app.config['MYSQL_DB'] = db


port = 465  # For SSL
smtp_server = "smtp.gmail.com"
sender_email = os.environ.get("MAIL_USERNAME") if (
    env != 'dev') else dev['MAIL_USERNAME']
password = os.environ.get("MAIL_PASSWORD") if (
    env != 'dev') else dev['MAIL_PASSWORD']

# Session config
app.secret_key = os.environ.get("client_secret") if(
    env != 'dev') else dev['client_secret']
app.config['SESSION_COOKIE_NAME'] = 'google-login-session'

mysql = MySQL(app)

clientSecret = os.environ.get("client_secret") if (
    env != 'dev') else dev['client_secret']
clientId = os.environ.get("client_id") if (env != 'dev') else dev['client_id']

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=clientId,
    client_secret=clientSecret,
    access_token_url='https://accounts.google.com/o/oauth2/token',
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    # This is only needed if using openId to fetch user info
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
    client_kwargs={'scope': 'openid email profile'},
)


def calculate_fines(user_id):
    cur = mysql.connection.cursor()
    cur.execute(
        'SELECT return_date,borrow_date FROM issue_details WHERE reader_id = {} and book_returned=1 AND return_date <> NULL;'.format(user_id))
    previousBookHistory = cur.fetchall()
    previousFines = 0
    curr_date = date.today()
    for val in previousBookHistory:
        if (val[0]-val[1]).days >= 10:
            previousFines = previousFines+((val[0]-val[1]).days-10)*2
    cur.execute(
        'SELECT borrow_date FROM issue_details WHERE reader_id = {} and book_returned=0;'.format(user_id))
    currentBookHistory = cur.fetchall()
    currentFines = 0
    curr_date = date.today()
    for val in currentBookHistory:
        if (curr_date-val[0]).days >= 10:
            currentFines = currentFines+((curr_date-val[0]).days-10)*2
    return currentFines+previousFines


@app.route("/")
def home():
    if "profile" in session:
        email = session["profile"]["email"]
        cur = mysql.connection.cursor()

        cur.execute(
            "SELECT * from librarian WHERE librarian_email='{}';".format(email))
        result = cur.fetchall()
        if (result):
            session["isAdmin"] = True
            return render_template('adminHome.html', details=session["profile"])
        else:
            session["isAdmin"] = False
            cur.execute(
                "SELECT is_faculty from reader WHERE reader_email = '{}';".format(email))
            result = cur.fetchone()
            if result == None:
                return render_template("register.html", email=session['profile']['email'], name=session['profile']['name'])
            else:
                if result[0] == 1:
                    session["isFaculty"] = True
                else:
                    session["isFaculty"] = False
                    cur.execute(
                        "SELECT ID FROM reader WHERE reader_email = '{}'".format(email))
                    user_id = cur.fetchone()[0]
                    cur.execute(
                        'SELECT reader_name, reader_email, ID FROM friendrequests INNER JOIN reader ON friendrequests.reader_1 = reader.ID WHERE reader_2 = {};'.format(user_id))
                    friendRequests = cur.fetchall()
                    session['friendRequests'] = friendRequests
                fines = calculate_fines(user_id)
                return render_template('userHome.html', details=session["profile"], friendRequests=session['friendRequests'], unpaid_fines=fines)
                # return render_template('userHome.html', details=session["profile"], friendRequests=session['friendRequests'])

    else:
        return render_template('Login.html')


@app.route("/sendmail")
def generate():
    if "profile" in session:
        email = session["profile"]["email"]
    else:
        return redirect('/')
    cur = mysql.connection.cursor()
    cur.execute(
        f"select return_date, ISBN, reader_id,last_reminder_sent_date from reminders")
    readers = cur.fetchall()
    for reader in readers:
        [return_date, ISBN, reader_id, last_reminder_sent_date] = reader
        cur.execute(f"SELECT reader_email FROM reader WHERE ID='{reader_id}'")
        person_email = cur.fetchone()
        today = date. today()
        delta = (last_reminder_sent_date-today).days
        mail_sent = []
        cur.execute(f"SELECT * FROM book WHERE ISBN='{ISBN}'")
        book = cur.fetchone()

        if delta % 1 == 0:
            mail_sent.append(reader_id)
            cur.execute(
                f"update reminders set last_reminder_sent_date='{today}' where ISBN='{ISBN}'")
            send_mail(
                person_email[0], "Subject: Reminder for returning book\n\n Your book, {} is overdue.Kindly return it.".format(book[0]))
            flash("Mail sent to {}".format(person_email[0]))
    return redirect('/')
# Register new student


def send_mail(receiver_email, message):
    print("Sending mail to " + receiver_email)
    print(message)
    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
        server.login(sender_email, password)
        server.sendmail(sender_email, receiver_email, message)


@app.route("/new", methods=["POST"])
def newStudent():
    data = request.form
    cur = mysql.connection.cursor()
    cur.execute("INSERT INTO reader (reader_name, reader_email, reader_address, phone_no, is_faculty) VALUES ('{}', '{}', '{}', {}, {});".format(
        data['Name'], data['Email'], data['Address'], data['Number'], 0))
    mysql.connection.commit()
    return redirect("/")


@app.route("/<memberType>")
def members(memberType):
    if session['isAdmin']:
        if memberType == 'students':
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT reader_name, reader_email, reader_address, phone_no, books_issued, unpaid_fines,ID FROM reader WHERE is_faculty = 0;")
            students = cur.fetchall()
            return render_template("member.html", people=students, memberType=memberType, details=session["profile"])
        if memberType == 'faculties':
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT reader_name, reader_email, reader_address, phone_no, books_issued, unpaid_fines,ID FROM reader WHERE is_faculty = 1;")
            faculties = cur.fetchall()
            return render_template("member.html", people=faculties, memberType=memberType, details=session["profile"])
    return redirect("/")


@app.route("/<memberType>/delete/<ID>")
def members1(memberType, ID):
    if session['isAdmin']:
        if memberType == 'faculties' or memberType == 'students':
            cur = mysql.connection.cursor()
            flash("Successfully deleted")
            cur.execute("DELETE FROM reader WHERE ID ={};".format(ID))
            mysql.connection.commit()
            return redirect("/{}".format(memberType))
    return redirect("/")


@app.route("/friend/delete/<ID>")
def friendDelete(ID):
    if "profile" in session:
        if session["isAdmin"] == True:
            redirect("/")
    else:
        redirect("/")
    email = session["profile"]["email"]
    cur = mysql.connection.cursor()
    cur.execute(f"SELECT ID FROM reader WHERE reader_email='{email}'")
    Me = cur.fetchone()

    cur.execute(
        "DELETE FROM friends WHERE reader_2 ={} AND reader_1 = {} ;".format(ID, Me[0]))
    cur.execute(
        "DELETE FROM friends WHERE reader_2 ={} AND reader_1 = {} ;".format(Me[0], ID))
    mysql.connection.commit()
    # return render_template("allFriends.html", pop = 1)
    msg = "Friend Table Successfully Updated"
    return redirect("/friends")


@app.route("/addFriend", methods=['GET', 'POST'])
def addFriend():
    if "profile" in session:
        email = session["profile"]["email"]
    else:
        return redirect('/')
    if session["isAdmin"] == True:
        redirect("/")
    if request.method == 'GET':
        return render_template('addFriend.html', msg="", details=session["profile"], friendRequests=session['friendRequests'])

    data = request.form
    if data['email'] == email:
        return render_template("addFriend.html", msg="Enter e-mail address of your friend", details=session["profile"], friendRequests=session['friendRequests'])
    cur = mysql.connection.cursor()

    cur.execute(
        "SELECT ID FROM reader WHERE reader_email='{}'".format(data['email']))
    friend = cur.fetchone()
    if friend == None:
        return render_template('addFriend.html', msg="Sorry no user exits with this email", details=session["profile"], friendRequests=session['friendRequests'])
    cur.execute(f"SELECT ID FROM reader WHERE reader_email='{email}'")
    Me = cur.fetchone()[0]
    friend = friend[0]
    # if person is librarian
    cur.execute(f"SELECT librarian_name FROM librarian WHERE librarian_email='{data['email']}'")
    isLibrarian = cur.fetchone()
    print("system checking")
    print(isLibrarian)
    print(data['email'])
    FlashEmail = data['email']
    if isLibrarian:
        flash("Sorry, " + FlashEmail + " is a Librarian","info")
        return redirect("/addFriend")
    cur.execute(
        "SELECT * FROM friends WHERE reader_1='{}' AND reader_2='{}'".format(email, data['email']))    
    if cur.fetchone() != None:
        return render_template('addFriend.html', msg="You are already friends with {}".format(data['email']), details=session["profile"], friendRequests=session['friendRequests'])

    try:
        cur.execute(f"SELECT ID FROM reader WHERE reader_email='{email}'")
        Me = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO friendrequests VALUES ({}, {});".format(Me, friend))
        mysql.connection.commit()
    except:
        return render_template('addFriend.html', msg="Already send request to {}, awaiting their response".format(data['email']), details=session["profile"], friendRequests=session['friendRequests'])
    return render_template('addFriend.html', msg="Friend request sent to {}".format(data['email']), details=session["profile"], friendRequests=session['friendRequests'])


@app.route("/request/cnf/<ID>")
def accept_request(ID):
    if "profile" in session:
        if session["isAdmin"] == True:
            return redirect("/")
        email = session["profile"]["email"]
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT ID FROM reader WHERE reader_email = '{}'".format(email))
        reader_id = cur.fetchone()[0]
        cur.execute("DELETE FROM friendrequests WHERE reader_1 = {} AND reader_2 = {}".format(
            ID, reader_id))
        cur.execute(
            "INSERT INTO friends VALUES ({}, {});".format(reader_id, ID))
        cur.execute(
            "INSERT INTO friends VALUES ({}, {});".format(ID, reader_id))
        mysql.connection.commit()
        flash("Friend request accepted")
        return redirect("/friends")
    return redirect("/")


@app.route("/request/del/<ID>")
def delete_request(ID):
    if "profile" in session:
        if session["isAdmin"] == True:
            return redirect("/")
        email = session["profile"]["email"]
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT ID FROM reader WHERE reader_email = '{}'".format(email))
        reader_id = cur.fetchone()[0]
        cur.execute("DELETE FROM friendrequests WHERE reader_1 = {} AND reader_2 = {}".format(
            ID, reader_id))
        mysql.connection.commit()
        flash("Friend request deleted")
        return redirect("/friends")
    return redirect("/")


@app.route("/book", methods=['GET', 'POST'])
def book():
    if request.method == 'GET':
        query = ""
        cur = mysql.connection.cursor()
        cur.execute(
            'SELECT ISBN, title, shelf_id, current_status, avg_rating, book_language, publisher, publish_date FROM book')
        books = cur.fetchall()
        if "isAdmin" in session:
            flash("Showing all books")
            if session["isAdmin"] == True:
                return render_template("adminSearchBook.html", books=books, details=session["profile"])
            if session["isAdmin"] == False:
                return render_template("userSearchBook.html", books=books, details=session["profile"], friendRequests=session['friendRequests'])

    if request.method == 'POST':
        data = request.form
        query = data['book']
        cur = mysql.connection.cursor()
        cur.execute(
            '''
            SELECT ISBN, title, shelf_id, current_status, avg_rating, book_language, publisher, publish_date FROM book WHERE title LIKE '%{}%'
            UNION
            SELECT ISBN, title, shelf_id, current_status, avg_rating, book_language, publisher, publish_date FROM book WHERE book_language LIKE '%{}%'
            UNION
            SELECT ISBN, title, shelf_id, current_status, avg_rating, book_language, publisher, publish_date FROM book WHERE publisher LIKE '%{}%'
            '''.format(query, query, query)
        )
        books = cur.fetchall()
        flash(Markup("Showing all results for:  <b>{}</b>".format(query)))
        if "isAdmin" in session:
            if session["isAdmin"] == True:
                return render_template("adminSearchBook.html", books=books, details=session["profile"])
            if session["isAdmin"] == False:
                return render_template("userSearchBook.html", books=books, details=session["profile"], friendRequests=session['friendRequests'])
    return redirect("/")


@app.route('/isbn/delete/<isbn>')
def deleteByISBN(isbn):
    if session['isAdmin']:
        cur = mysql.connection.cursor()
        cur.execute("DELETE FROM book WHERE ISBN = {}".format(isbn))
        flash("Book successfully deleted")
        mysql.connection.commit()
        return redirect("/book")
    return redirect("/")


@app.route('/isbn/hold/<isbn>')
def holdByISBN(isbn):
    if "profile" in session:
        if session["isAdmin"] == True:
            return redirect("/")
        # Only for readers
        email = session["profile"]["email"]
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT ID, books_issued, unpaid_fines FROM reader WHERE reader_email = '{}'".format(email))
        [reader_id, books_issued, unpaid_fines] = cur.fetchone()
        if session['isFaculty'] == False:
            if books_issued == 3 or unpaid_fines > 1000:
                if books_issued == 3:
                    flash(
                        "You already have issued 3 books so now you cannot issue more", "info")
                else:
                    flash("Please pay your unpaid fines first", "info")
                return redirect("/book")
        cur.execute(
            "UPDATE reader SET books_issued = books_issued+1 WHERE ID={}".format(reader_id))
        cur.execute(
            "UPDATE book SET current_status = 'soldout' WHERE ISBN={}".format(isbn))
        cur.execute("INSERT INTO issue_details VALUES ({}, {}, CURDATE(),0,0,NULL)".format(
            reader_id, isbn))
        mysql.connection.commit()
        return redirect("/book")
    return redirect("/")


@app.route('/isbn/putOnhold/<isbn>')
def putOnHoldByISBN(isbn):
    if "profile" in session:
        if session["isAdmin"] == True:
            return redirect("/")
        # Only for readers
        email = session["profile"]["email"]
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT ID, books_issued, unpaid_fines FROM reader WHERE reader_email = '{}'".format(email))
        [reader_id, books_issued, unpaid_fines] = cur.fetchone()
        if session['isFaculty'] == False:
            if books_issued == 3 or unpaid_fines > 1000:
                if books_issued == 3:
                    flash(
                        "You already have issued 3 books so now you cannot issue more", "info")
                else:
                    flash("Please pay your unpaid fines first", "info")
                return redirect("/book")
        cur.execute(
            "UPDATE book SET current_status = 'hold' WHERE ISBN={}".format(isbn))
        cur.execute(
            "INSERT INTO  holds VALUES ({}, {},NOW())".format(isbn, reader_id))
        mysql.connection.commit()
        return redirect("/book")
    return redirect("/")


@app.route('/isbn/unhold/<isbn>')
def unholdByISBN(isbn):
    if "profile" in session:
        email = session["profile"]["email"]
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT ID, books_issued FROM reader WHERE reader_email = '{}'".format(email))
        [reader_id, books_issued] = cur.fetchone()
        books_issued -= 1
        today = date.today()
        if books_issued < 0:
            books_issued = 0
        cur.execute("UPDATE reader SET books_issued = {} WHERE ID={}".format(
            books_issued, reader_id))
        cur.execute(
            "select current_status from book WHERE ISBN={}".format(isbn))
        [current_status] = cur.fetchone()
        # if current_status=="hold":
        #     redirect("/isbn/hold/<isbn>")
        today = date.today()
        cur.execute(
            "UPDATE book SET current_status = 'available' WHERE ISBN={}".format(isbn))
        cur.execute(
            "UPDATE issue_details SET book_returned = 1 , return_date='{}' WHERE ISBN={}".format(today, isbn))
        mysql.connection.commit()
        flash("You have successfully returned book to library", "info")
        return redirect("/book")
    return redirect("/")


@app.route("/logs")
def logs():
    if "profile" in session:
        if session["isAdmin"] == False:
            return redirect("/")
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM issue_details;")
        details = cur.fetchall()
        return render_template("issueDetails.html", details=details)
    return redirect("/")


@app.route("/previousReadings")
def previousReadings():
    if "profile" in session:
        if session["isAdmin"] == True:
            return redirect("/")
        email = session["profile"]["email"]
        cur = mysql.connection.cursor()
        cur.execute('''
        SELECT book.ISBN, title, borrow_date, ratings FROM issue_details
        INNER JOIN reader ON issue_details.reader_id = reader.ID
        INNER JOIN book ON issue_details.ISBN = book.ISBN
        WHERE
        reader_email = "{}" AND book_returned=1 ;
        '''.format(email))
        details = cur.fetchall()
        return render_template("issueDetailsU.html", details=session["profile"], issueDetails=details, friendRequests=session['friendRequests'])
    return redirect("/")

@app.route("/ratings/<isbn>", methods=['POST'])
def update_ratings(isbn):
    if "profile" in session:
        if not session['isAdmin']:
            email = session["profile"]["email"]
            data = request.form;
            cur = mysql.connection.cursor()
            try:
                cur.execute("SELECT ID FROM reader WHERE reader_email='{}'".format(email))
                reader_id = cur.fetchone()[0]
                cur.execute("UPDATE issue_details SET ratings={} WHERE ISBN={} AND reader_id={}".format(data['rate'], isbn, reader_id))
                mysql.connection.commit()
            except:
                flash("Something went wrong")
            return redirect('/previousReadings')
    return redirect("/")
    
@app.route("/addnewfaculty",methods=['GET','POST'])
def addnewfaculty(): 
     if session['isAdmin']:
        if request.method == 'GET':
            return render_template("faculty_form.html", details=session["profile"])
        else:
            data = request.form
            cur = mysql.connection.cursor()
            # Email = {data['email']}
            cur.execute(f"SELECT reader_name FROM reader WHERE reader_email='{data['email']}'")
            Person = cur.fetchall()
            if Person:
                flash("Sorry, a reader already exist with email:  {}".format(data['email']),"info")
                # flash("  Person[0])
                return redirect("/addnewfaculty")
            cur.execute(
                f"insert into reader(reader_name,reader_hash_password,reader_email,reader_address,phone_no,is_faculty,ID,unpaid_fines,books_issued) values('{data['faculty_name']}','{data['hashpassword']}','{data['email']}','{data['address']}','{data['number']}','1','','0','0')")    
            mysql.connection.commit()
            flash("New Faculty Added!!!")
        return redirect("/faculties") 


@app.route("/addBook", methods=['GET', 'POST'])
def addBook():
    if session['isAdmin']:
        if request.method == 'GET':
            return render_template("addBook.html", details=session["profile"])
        else:
            data = request.form
            cur = mysql.connection.cursor()
            # print(data['isbn'])
            # cur.execute(f"SELECT title FROM book WHERE ISBN='{data['isbn']}'")
            # count =  cur.fetchall()
            # Improving Complexity 
            if True:    
                try:
                    cur.execute(f"INSERT INTO book(ISBN, title, book_language, publisher, publish_date, shelf_id) VALUES({data['isbn']}, '{data['title']}', '{data['language']}', '{data['publisher']}', '{data['date']}', {data['shelf']})")
                    data = data.to_dict(flat=False)
                    for tag in data['tags']:
                        cur.execute("INSERT INTO tags VALUES ({}, '{}')".format(data['isbn'][0], tag))
                    mysql.connection.commit()
                    flash("New Book Added")
                    return redirect("/book")
                except:
                    flash("Please check, a book already exist with same ISBN in our library","info")
                    return redirect("/addBook")
                return redirect("/")     
    return redirect("/")


@app.route("/myBooks")
def myBooks():
    if "profile" in session:
        email = session["profile"]["email"]
    else:
        return redirect("/")
    cur = mysql.connection.cursor()
    cur.execute(f"SELECT ID FROM reader WHERE reader_email='{email}'")
    person = cur.fetchone()
    cur.execute(
        f"SELECT ISBN,title,avg_rating,book_language,publisher,publish_date,current_status FROM book WHERE ISBN in( SELECT ISBN FROM issue_details WHERE reader_id='{person[0]}' and book_returned=0)")
    books = cur.fetchall()
    return render_template('myBooks.html', books=books, details=session["profile"], friendRequests=session['friendRequests'])


@app.route("/shelf")
def shelf():
    if session["isAdmin"] == True:
        cur = mysql.connection.cursor()
        cur.execute(
            "SELECT shelf_id, capacity FROM shelf;")
        shelfs = cur.fetchall()

        return render_template('shelf.html', shelfs=shelfs, details=session["profile"])
    return redirect("/")


@app.route("/recommendedBooks")
def user_BookRecommedation():

    if "profile" in session:
        email = session["profile"]["email"]
    else:
        return redirect("/")
    cur = mysql.connection.cursor()
    cur.execute(f"SELECT ID FROM reader WHERE reader_email='{email}'")
    person = cur.fetchone()
    cur.execute(
        f"select * from book where ISBN in(select ISBN from tags where tag_name in(select tag_name from tags where ISBN in (select ISBN from issue_details where reader_id = '{person[0]}'))) order by avg_rating DESC")
    books = cur.fetchall()
    zeroes = 1 if len(books) == 0 else 0
    return render_template('user_BookRecommedation.html', books=books, zeroes=zeroes, details=session["profile"], friendRequests=session['friendRequests'])


@ app.route("/booksWithTags")
def user_booksWithTags():
    return render_template('booksWithTags.html', details=session["profile"])


@ app.route("/friends")
def friends():
    if "profile" in session:
        email = session["profile"]["email"]
    else:
        return redirect("/")

    cur = mysql.connection.cursor()
    cur.execute(
        "SELECT reader_name,reader_email,phone_no,books_issued,ID  FROM reader WHERE ID IN ( SELECT reader_2 FROM friends WHERE reader_1 IN (SELECT ID FROM reader WHERE reader_email='{}') )".format(email))
    friendinfo = cur.fetchall()

    return render_template('allFriends.html', len=len(friendinfo), friendinfo=friendinfo, details=session["profile"], friendRequests=session['friendRequests'])


@ app.route("/feedback")
def feedback():
    return render_template('userFeedback.html')


@ app.route("/history")
def user_History():
    if "profile" in session:
        email = session["profile"]["email"]
        if session["isAdmin"] == True:
            return redirect("/")
    else:
        return redirect("/")
    # user and logged
    # fetch ID from email of user
    cur = mysql.connection.cursor()
    cur.execute(f"SELECT ID FROM reader WHERE reader_email='{email}'")
    person = cur.fetchone()
    cur.execute(
        f"SELECT ISBN, borrow_date , book_returned FROM issue_details WHERE reader_id='{person[0]}'")
    data = cur.fetchall()

    return render_template('userHistory.html', data=data, details=session["profile"], friendRequests=session['friendRequests'])



@ app.route("/myfines")
def myfines():
    if "profile" in session:
        email = session["profile"]["email"]
        if session["isAdmin"] == True:
            return redirect("/")
    else:
        return redirect("/")
    # user and logged
    # fetch ID from email of user
    cur = mysql.connection.cursor()
    cur.execute(f"SELECT ID FROM reader WHERE reader_email='{email}'")
    person = cur.fetchone()
    curr_date = date.today()
    
    cur.execute(
        f"SELECT ISBN, borrow_date , book_returned,return_date FROM issue_details WHERE reader_id='{person[0]}'")
    data = cur.fetchall()
    # print(curr_date, data)
    return render_template('myFines.html', data=data, date=curr_date)



@ app.route("/tables")
def addBooks():
    return render_template('tables.html', details=session["profile"])


@ app.route("/dashboard")
def dashboard():
    return render_template('dashboard.html', details=session["profile"])


@ app.route('/login')
def login():
    google = oauth.create_client('google')  # create the google oauth client
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri)


@ app.route('/authorize')
def authorize():
    message = None
    google = oauth.create_client('google')
    # Access token from google (needed to get user info)
    token = google.authorize_access_token()
    # userinfo contains stuff u specificed in the scrope
    resp = google.get('userinfo')
    user_info = resp.json()
    user = oauth.google.userinfo()
    session['profile'] = user_info
    # make the session permanant so it keeps existing after browser gets closed
    session.permanent = True
    if token != '':
        message = Markup('<b>Login</b> successfull')
    else:
        message = 'You Please Try Again'
    flash(message)
    return redirect('/')


@ app.route("/logout")
def logout():
    for key in list(session.keys()):
        session.pop(key)
    flash(Markup("You were successfully <b>logged out</b>"))
    return redirect("/")


@ app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html')


if __name__ == "__main__":
    if(env == 'dev'):
        app.run(debug=True)
    else:
        app.run()
