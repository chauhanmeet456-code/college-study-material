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
- Create account and login
- View unlimited materials
- Download materials
- Add/remove favorites
- Update profile
- Change password
- Upload profile picture

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
- Upload files: PDF, DOC, DOCX, PPT, PPTX, ZIP, TXT
- Maximum file size: 50MB
- Categorize materials by type
- Associate materials with subjects
- Mark materials as featured
- View/download counts tracking

### User Management
- User registration with email validation
- Secure password hashing
- User activation/deactivation
- Profile management

### Search & Discovery
- Full-text search
- Filter by subject, category, file type
- Sort by latest, popular, downloads, views
- Search suggestions

### Guest Limits
- View limit: 2 materials
- Download limit: 2 materials
- Encourages registration

## Non-Functional Requirements
- Responsive design (mobile, tablet, desktop)
- Dark mode support
- Fast page loads
- Secure file uploads
- SQLite/MySQL database support

## Success Metrics
- User registrations
- Material uploads
- Search usage
- Download counts
- User engagement
