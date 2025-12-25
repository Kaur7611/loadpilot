from flask import Flask, render_template, request, redirect, url_for, flash, abort, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from collections import Counter
import csv
from io import StringIO
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'logistics-command-key-99' # Stronger key in prod
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/loadpilot_pro.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# -- PREMIUM MODELS --

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

class Driver(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    truck_number = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20))
    status = db.Column(db.String(20), default="Available") # Available, On Load, Off Duty
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Load(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    load_ref = db.Column(db.String(20)) # e.g. "LD-4022" - Custom ID
    pickup = db.Column(db.String(100), nullable=False)
    drop = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(20))
    rate = db.Column(db.Float, default=0.0) # $$$ Money Field
    status = db.Column(db.String(50), default="Pending") # Pending, In Transit, Delivered, Cancelled
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'))
    driver = db.relationship('Driver', backref='loads') # Easy access to driver name
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    
    
    with app.app_context():
      db.create_all()

# -- AUTH LOGIC --

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def home():
    return redirect(url_for('dashboard'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            flash('⚠️ Username taken. Try another.', 'error')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        
        flash('✅ Account created. Welcome aboard.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash("❌ Invalid credentials.", 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# -- DASHBOARD (THE COMMAND CENTER) --

@app.route('/dashboard')
@login_required
def dashboard():
    # 1. Base Query (Filtered by User for Security)
    query = Load.query.filter_by(user_id=current_user.id)
    
    # 2. Filters (Database level for speed)
    status_filter = request.args.get("status", "All")
    search_query = request.args.get("search", "")
    
    if status_filter != "All":
        query = query.filter_by(status=status_filter)
        
    if search_query:
        search = f"%{search_query}%"
        # Search in Pickup, Drop, or Load Ref
        query = query.filter(
            (Load.pickup.ilike(search)) | 
            (Load.drop.ilike(search)) |
            (Load.load_ref.ilike(search))
        )

    loads = query.order_by(Load.id.desc()).all()
    drivers = Driver.query.filter_by(user_id=current_user.id).distinct().all()

    # 3. Analytics (Real Market Metrics)
    all_user_loads = Load.query.filter_by(user_id=current_user.id).all()
    status_counts = Counter([l.status for l in all_user_loads])
    total_revenue = sum([l.rate for l in all_user_loads])
    active_loads = status_counts['In Transit'] + status_counts['Pending']

    return render_template("dashboard.html",
                           drivers=drivers,
                           loads=loads,
                           status_counts=status_counts,
                           status_filter=status_filter,
                           search_query=search_query,
                           total_revenue=total_revenue,
                           active_loads=active_loads)

# -- DRIVER MANAGEMENT --

@app.route('/add_driver', methods=['GET', 'POST'])
@login_required
def add_driver():
    if request.method == 'POST':
        new_driver = Driver(
            name=request.form['name'],
            truck_number=request.form['truck_number'],
            phone=request.form['phone'],
            status="Available",
            user_id=current_user.id
        )
        db.session.add(new_driver)
        db.session.commit()
        flash('driver added', 'success') # Simplified for toast logic
        return redirect(url_for('dashboard'))
    return render_template('add_driver.html')

@app.route('/edit_driver/<int:driver_id>', methods=['GET', 'POST'])
@login_required
def edit_driver(driver_id):
    driver = Driver.query.get_or_404(driver_id)
    
    # Security Check: Ensure the driver belongs to the current user
    if driver.user_id != current_user.id:
        abort(403)

    if request.method == 'POST':
        driver.name = request.form.get('name')
        driver.truck_number = request.form.get('truck_number')
        driver.phone = request.form.get('phone')
        
        db.session.commit()
        flash('Driver updated successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('edit_driver.html', driver=driver)

@app.route('/delete_driver/<int:driver_id>')
@login_required
def delete_driver(driver_id):
    driver = Driver.query.get_or_404(driver_id)
    # SECURITY CHECK
    if driver.user_id != current_user.id:
        abort(403)
    
    db.session.delete(driver)
    db.session.commit()
    return redirect(url_for('dashboard'))

# -- LOAD MANAGEMENT --

@app.route('/add_load', methods=['GET', 'POST'])
@login_required
def add_load():
    drivers = Driver.query.filter_by(user_id=current_user.id).all()
    if request.method == 'POST':
        # Auto-generate a Load Ref if empty (Simple logic)
        import random
        ref_id = f"LD-{random.randint(1000, 9999)}"
        
        new_load = Load(
            load_ref=ref_id,
            pickup=request.form['pickup'],
            drop=request.form['drop'],
            date=request.form['date'],
            rate=float(request.form.get('rate', 0)), # Handle Money
            status=request.form['status'],
            driver_id=request.form.get('driver_id') or None,
            user_id=current_user.id
        )
        db.session.add(new_load)
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('add_load.html', drivers=drivers)

@app.route('/edit_load/<int:load_id>', methods=['GET', 'POST'])
@login_required
def edit_load(load_id):
    load = Load.query.get_or_404(load_id)
    # SECURITY CHECK
    if load.user_id != current_user.id:
        abort(403)

    drivers = Driver.query.filter_by(user_id=current_user.id).all()
    
    if request.method == 'POST':
        load.pickup = request.form['pickup']
        load.drop = request.form['drop']
        load.date = request.form['date']
        load.rate = float(request.form.get('rate', 0))
        load.status = request.form['status']
        load.driver_id = request.form.get('driver_id') or None
        db.session.commit()
        flash('Load updated successfully', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('edit_load.html', load=load, drivers=drivers)

@app.route('/delete_load/<int:load_id>')
@login_required
def delete_load(load_id):
    load = Load.query.get_or_404(load_id)
    if load.user_id != current_user.id:
        abort(403)
        
    db.session.delete(load)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/export_loads')
@login_required
def export_loads():
    # Only export CURRENT USER'S loads
    loads = Load.query.filter_by(user_id=current_user.id).all()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Load Ref', 'Pickup', 'Drop', 'Rate ($)', 'Date', 'Status', 'Driver']) 

    for load in loads:
        d_name = load.driver.name if load.driver else "Unassigned"
        cw.writerow([load.load_ref, load.pickup, load.drop, load.rate, load.date, load.status, d_name])

    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=my_loads_export.csv"}
    )

if __name__ == '__main__':
     app.run(debug=True)
    
   