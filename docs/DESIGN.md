# Design Decisions & Rationale

## Database Choice: SQLite vs MySQL

### Decision: Support both SQLite and MySQL
- SQLite as default for simplicity and portability
- MySQL available for production scalability
- Configuration via `USE_SQLITE` environment variable

### Rationale
- SQLite requires no separate server
- Easier for development and testing
- MySQL better for concurrent users in production

## Authentication: Session-Based

### Decision: Flask session-based authentication
- Store `user_id` and `admin_id` in session
- Separate sessions for users and admins

### Rationale
- Simple to implement
- No JWT complexity
- Sufficient for this application scale
- Easy to logout by clearing session

## File Storage: Local Filesystem

### Decision: Store uploads in `static/uploads/`
- Category-based subdirectories
- UUID-based filenames

### Rationale
- No external storage service needed
- Simple backup process
- Fast access for small to medium scale
- Easy to migrate to S3/Cloud later

## Blueprint Organization

### Decision: Separate blueprints by user type
- `main_bp`: Public routes
- `auth_bp`: Authentication
- `user_bp`: User dashboard
- `admin_bp`: Admin panel

### Rationale
- Clear separation of concerns
- Easy to maintain
- Logical grouping of routes
- Independent scaling possible

## Template Structure

### Decision: Extend base.html
- Single base template for public site
- Separate base.html for admin panel
- Block-based inheritance

### Rationale
- DRY principle
- Consistent navigation/footer
- Easy to update global styles
- Admin panel has different layout needs

## Guest Activity Tracking

### Decision: Session-based guest tracking
- `guest_session_id` stored in session
- Track views and downloads in database

### Rationale
- Encourage registration
- Fair usage policy
- No complex analytics needed
- Simple implementation

## Search Implementation

### Decision: Server-side search with AJAX
- API endpoint `/api/search`
- Debounced input
- Real-time suggestions

### Rationale
- Better performance than client-side
- SQL LIKE queries sufficient
- AJAX provides better UX
- Suggestions improve discoverability

## CSS Framework: Bootstrap 5

### Decision: Bootstrap 5.3.2 with custom CSS
- Responsive grid system
- Pre-built components
- Custom theme with CSS variables
- Dark mode support

### Rationale
- Rapid development
- Consistent UI
- Mobile-first responsive
- Large component library
- Dark mode built-in

## Password Security

### Decision: Werkzeug password hashing
- `generate_password_hash()` for storage
- `check_password_hash()` for verification
- Minimum 6 characters

### Rationale
- Industry standard
- Secure by default
- No plain text storage
- Salt automatically handled

## Error Handling

### Decision: Custom error pages + logging
- 404 and 500 static HTML pages
- Flash messages for user feedback
- Error handlers in app.py

### Rationale
- Better user experience
- Professional appearance
- Debug mode for development
- Graceful degradation

## Responsive Design

### Decision: Mobile-first approach
- Breakpoints: 576px, 768px, 992px, 1200px
- Collapsible navigation
- Responsive tables
- Touch-friendly buttons

### Rationale
- Mobile usage is primary
- Better SEO
- Progressive enhancement
- Accessible design
