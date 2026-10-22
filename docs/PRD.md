# Product Requirements Document - StudyHub

## Overview
StudyHub is a college study material management platform designed to help students access and share academic resources including notes, assignments, books, practicals, question papers, and more.

## Target Users
- Students (primary users)
- Admin/Teachers (content managers)

## Core Features

### 1. Public Access (Guest Users)
- Browse subjects and study materials
- View limited materials (guest limits apply)
- Search and filter materials
- View material details

### 2. Student Features (Registered Users)
- Register and create account
- Login with OTP verification (email or development OTP without SMTP)
- Logout
- Single active session with multi-device conflict detection
- View unlimited materials
- Download materials
- Add/remove favorites
- Update profile
- Upload profile picture
- Change password
- Forgot password via OTP reset
- Request account deletion with OTP confirmation
- View login history and active sessions
- View PDFs, DOC/DOCX/TXT inline, PPT/PPTX download, video playback

### 3. Admin Features
- Admin dashboard with statistics
- Manage subjects (add/edit/delete)
- Manage materials (upload/edit/delete)
- Manage categories
- Manage users (activate/deactivate/delete)
- View and manage contact messages
- Create/manage announcements

## Functional Requirements

### Material Management
- Upload files: PDF, DOC, DOCX, PPT, PPTX, ZIP, TXT, images (PNG/JPG/JPEG/GIF/WebP), video (MP4, WebM, AVI, MOV, etc.)
- Maximum file size: 5 GB
- Categorize materials by type
- Associate materials with subjects
- Mark materials as featured
- View/download counts tracking

### User Management
- User registration (no email verification required)
- Secure password hashing
- OTP-based login verification
- User activation/deactivation
- Profile management

### Search & Discovery
- Full-text search (title and description)
- Filter by subject, category, file type
- Sort by latest, oldest, most downloaded, most viewed, A-Z
- Search suggestions via `/api/search/suggestions`
- AJAX-powered search with pagination

### Guest Limits
- View limit: 4 materials
- Download limit: 4 materials
- Encourages registration

## Non-Functional Requirements
- Responsive design (mobile, tablet, desktop)
- Dark mode support
- Fast page loads
- Secure file uploads
- SQLite/MySQL database support
- CSRF protection on all state-changing operations
- Login rate limiting to prevent brute-force attacks
- Server-side session validation with token hashing
- Password hashing (PBKDF2)
- Security response headers (CSP, X-Frame-Options, etc.)

## Success Metrics
- User registrations
- Material uploads
- Search usage
- Download counts
- User engagement
