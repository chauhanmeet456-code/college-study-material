# Project Memory - StudyHub

## Key Decisions

### Database
- Default: SQLite (file-based, no server needed)
- Alternative: MySQL (for production)
- Toggle: `USE_SQLITE=true` in `.env`

### Authentication
- Session-based (not JWT)
- Separate sessions for users and admins
- Password hashing with Werkzeug

### File Uploads
- Local storage in `static/uploads/`
- UUID filenames to prevent collisions
- Category-based organization

### Frontend
- Bootstrap 5.3.2
- Custom CSS with variables
- Dark mode support
- Mobile-first responsive

## Important Patterns

### Database Queries
```python
# Always use parameterized queries
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

# Close connections in finally blocks
finally:
    close_cursor(cursor)
    conn.close()
```

### Route Protection
```python
@user_required
def protected_route():
    # Only accessible to logged-in users
    pass

@admin_required
def admin_route():
    # Only accessible to admins
    pass
```

### File Upload
```python
if file and allowed_file(file.filename):
    filename = generate_safe_filename(file.filename)
    file.save(os.path.join(upload_dir, filename))
```

## Common Pitfalls

1. **404.html Location**: Must be in `static/` folder, not `templates/`
2. **SQLite vs MySQL**: Use `?` for SQLite, `%s` for MySQL
3. **Session Keys**: Use `user_id` for users, `admin_id` for admins
4. **Template Context**: Always pass `current_user` to templates
5. **Guest Limits**: Check `get_current_user()` before applying limits

## Dependencies

### Core
- Flask==3.0.0
- PyMySQL==1.1.0
- python-dotenv==1.0.0
- Werkzeug==3.0.1
- gunicorn==21.2.0
- python-magic==0.4.27

### Frontend (CDN)
- Bootstrap 5.3.2
- Font Awesome 6.5.1
- Google Fonts (Inter)

## Environment Variables

### Required
```env
SECRET_KEY=your_secret_key_here
DB_HOST=localhost
DB_PORT=3306
DB_NAME=college_study
DB_USER=root
DB_PASSWORD=your_password
USE_SQLITE=true
```

### Optional
```env
GUEST_VIEW_LIMIT=4
GUEST_DOWNLOAD_LIMIT=4
MAX_CONTENT_LENGTH=5368709120
```

## File Structure Reminders

### Uploads
```
static/uploads/
├── notes/
├── assignments/
├── books/
├── practicals/
├── question_papers/
├── important_questions/
├── syllabus/
├── other/
└── profiles/
```

### Templates
```
templates/
├── base.html (public site)
├── admin/base.html (admin panel)
└── [page].html (extends appropriate base)
```

## Database Schema

### Key Tables
- `users` - Student accounts
- `admins` - Admin accounts
- `subjects` - Academic subjects
- `categories` - Material categories
- `materials` - Study materials
- `favorites` - User favorites
- `contact_messages` - Contact form submissions
- `announcements` - Site announcements
- `guest_views` - Guest view tracking
- `guest_downloads` - Guest download tracking

## Testing Checklist

### Manual Tests
- [ ] User registration
- [ ] User login/logout
- [ ] Admin login
- [ ] Browse subjects
- [ ] View material
- [ ] Search materials
- [ ] Upload material (admin)
- [ ] Add to favorites
- [ ] Update profile
- [ ] Contact form

### Automated Tests
- [ ] Unit tests for models
- [ ] Route tests
- [ ] API tests
- [ ] Database tests

## Deployment Notes

### Development
```bash
python app.py
# Runs on http://127.0.0.1:5000
```

### Production
```bash
gunicorn -w 4 app:create_app()
# Requires proper environment variables
```

### Database Init
```bash
python init_sqlite.py
# Only when USE_SQLITE=true
```

## Contact & Support
- Project: College Study Material Management System
- Tech: Flask, Bootstrap, SQLite/MySQL
- Status: Active Development
