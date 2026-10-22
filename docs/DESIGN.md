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

## Authentication: Session-Based with OTP

### Decision: Flask session-based authentication with OTP verification
- Two-step login: password verification, then OTP verification
- OTP sent via email (SMTP) or flashed to user in development mode
- Session-based with `user_id` and `admin_id` stored in the Flask session cookie
- Server-side session validation: a SHA-256 hash of a random session token is
  stored in the `user_sessions` table and validated on every protected request via
  `before_request`
- Separate sessions for users and admins
- Single active session per user with multi-device conflict detection

### Rationale
- Simple to implement
- No JWT complexity
- Sufficient for this application scale
- Easy to logout by clearing session
- OTP adds a second factor without requiring a separate app
- Server-side validation means a stolen/replayed cookie cannot access a revoked session
- Single active session prevents session sharing and improves security

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
- API endpoint `/api/search` with filters (subject, category, file_type) and sort options
- `/api/search/suggestions` for real-time autocomplete suggestions
- Debounced input
- Returns JSON with paginated results

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

## Session Security & CSRF Protection

### Decision: Server-side sessions with token hashing and CSRF tokens
- Random session token generated on each login (not reused from pre-auth session)
- Only the SHA-256 hash of the token is persisted in `user_sessions`; the raw token lives solely in the signed Flask cookie
- `before_request` validates the server-side session on every protected request: checks `status='active'`, absolute `expires_at`, and idle timeout
- Single active session: when a new login would exceed `MAX_ACTIVE_SESSIONS_PER_USER`, the old session is revoked inside an atomic transaction (`BEGIN IMMEDIATE` for SQLite)
- CSRF token generated per session and validated on all POST/PUT/PATCH/DELETE routes via `@csrf_protect`
- Login rate limiting: 5 attempts per 15-minute window per identifier and IP
- Security response headers: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy
- Session cookies: HttpOnly=True, SameSite=Lax, Secure configurable via env

### Rationale
- A revoked or expired session cannot be reused even if the cookie is retained
- CSRF tokens prevent cross-site request forgery on state-changing operations
- Rate limiting mitigates brute-force attacks on the login endpoint
- Security headers provide defense-in-depth against common browser-based attacks

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
