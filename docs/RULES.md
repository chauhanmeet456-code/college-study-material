# Development Rules & Conventions

## Code Style

### Python
- Use 4 spaces for indentation
- Follow PEP 8 naming conventions
- Docstrings for all public functions/classes
- Type hints preferred but not required
- Maximum line length: 100 characters

### Naming Conventions
- **Files**: snake_case (e.g., `guest_activity.py`)
- **Classes**: PascalCase (e.g., `ContactMessage`)
- **Functions/Methods**: snake_case (e.g., `get_current_user`)
- **Variables**: snake_case (e.g., `material_id`)
- **Constants**: UPPER_SNAKE_CASE (e.g., `MAX_CONTENT_LENGTH`)
- **Routes**: kebab-case URLs (e.g., `/question-papers`)

### Database
- Table names: snake_case plural (e.g., `contact_messages`)
- Column names: snake_case (e.g., `is_featured`)
- Foreign keys: `<table>_id` (e.g., `subject_id`)
- Timestamps: `created_at`, `updated_at`

## Security Rules

### Authentication
- All admin routes must use `@admin_required`
- All user routes must use `@user_required`
- Passwords must be hashed with `werkzeug.security`
- Never store plain text passwords

### Input Validation
- Validate all form inputs
- Sanitize file uploads with `secure_filename()`
- Check file types against whitelist
- Limit file sizes

### SQL Injection Prevention
- Always use parameterized queries
- Never use string formatting for SQL
- Use `?` for SQLite, `%s` for MySQL

## Frontend Rules

### HTML
- Extend `base.html` for consistency
- Use Bootstrap 5 classes
- Include Font Awesome icons
- Responsive design required

### CSS
- Use CSS custom properties (variables)
- Mobile-first approach
- Support dark mode
- Avoid inline styles where possible

### JavaScript
- Use IIFE pattern for modules
- Event delegation where appropriate
- Error handling for all async operations
- Console.log only in development

## API Design

### REST Conventions
- GET: Retrieve resources
- POST: Create resources
- PUT/PATCH: Update resources
- DELETE: Delete resources

### Response Format
- JSON for all API responses
- Consistent error format: `{"success": false, "message": "..."}`
- Success format: `{"success": true, "data": ...}`

### Status Codes
- 200: Success
- 201: Created
- 400: Bad request
- 401: Unauthorized
- 403: Forbidden
- 404: Not found
- 500: Server error

## Database Rules

### Migrations
- SQLite: Use `init_sqlite.py` for initialization
- MySQL: Manual schema creation
- No automatic migrations

### Queries
- Use JOINs instead of multiple queries
- Index frequently queried columns
- Use LIMIT for pagination
- Close connections in finally blocks

## Git Workflow
- Feature branches for new features
- Descriptive commit messages
- No secrets in commits
- Review before merging

## Testing
- Test all routes manually
- Verify database operations
- Check file uploads
- Test authentication flows
- Verify responsive design

## Deployment
- Use environment variables for configuration
- Never commit `.env` file
- Use `gunicorn` for production
- Enable SQLite for simple deployments
- Set `USE_SQLITE=true` in `.env`
