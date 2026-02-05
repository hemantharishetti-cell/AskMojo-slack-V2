# Admin Panel Frontend

This directory contains the admin panel frontend files.

## Structure

- `index.html` - Login page
- `admin.html` - Admin dashboard
- `css/style.css` - Styling (red/black/white theme)
- `js/auth.js` - Authentication management
- `js/api.js` - API client
- `js/login.js` - Login functionality
- `js/admin.js` - Admin dashboard functionality

## Access

1. Start the FastAPI server: `python run.py`
2. Navigate to: `http://localhost:8000/` or `http://localhost:8000/static/index.html`
3. Login with admin credentials

## Features

- **Login Page**: Secure authentication with JWT tokens
- **Dashboard**: Statistics overview (users, documents, queries)
- **User Management**: View, edit, and delete users
- **Responsive Design**: Mobile-friendly interface
- **Professional Theme**: Red, black, and white color scheme

## API Endpoints Used

- `POST /api/v1/auth/login-json` - User login
- `GET /api/v1/auth/me` - Get current user
- `GET /api/v1/admin/stats` - Dashboard statistics
- `GET /api/v1/admin/users` - List all users
- `GET /api/v1/admin/users/{id}` - Get user details
- `PUT /api/v1/admin/users/{id}` - Update user
- `DELETE /api/v1/admin/users/{id}` - Delete user

