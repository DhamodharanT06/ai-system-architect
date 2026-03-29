# AI System Architect - Deployment Guide

## Overview

This guide covers deploying AI System Architect to production environments.

## Prerequisites

- Docker and Docker Compose (recommended)
- Or: Python 3.8+, Node.js 16+
- Groq API Key
- Hosting platform account (Heroku, Railway, Render, AWS, etc.)

## Option 1: Docker Compose (Recommended)

### Local Deployment with Docker

```bash
# 1. Ensure Docker and Docker Compose are installed
docker --version
docker-compose --version

# 2. Create .env file with Groq API Key
cat > .env << EOF
GROQ_API_KEY=your_groq_api_key_here
EOF

# 3. Build and run containers
docker-compose up -d

# 4. Access application
# Frontend: http://localhost:3000
# API: http://localhost:8000
# Docs: http://localhost:8000/docs

# 5. View logs
docker-compose logs -f

# 6. Stop services
docker-compose down
```

## Option 2: Deploy to Render.com

### Backend Deployment

1. **Push to GitHub**
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/yourusername/ai-architect.git
git push -u origin main
```

2. **Create Backend Service on Render**
   - Go to [render.com](https://render.com)
   - Click "New +" → "Web Service"
   - Connect GitHub repository
   - Select the backend folder
   - Set environment:
     - Name: `ai-architect-backend`
     - Runtime: Python 3.11
     - Build Command: `pip install -r requirements.txt`
     - Start Command: `uvicorn main:app --host 0.0.0.0 --port 8000`
   - Add environment variable:
     - `GROQ_API_KEY` = your_groq_api_key

3. **Frontend Deployment**
   - Create another Web Service
   - Select the frontend folder
   - Set environment:
     - Name: `ai-architect-frontend`
     - Runtime: Node
     - Build Command: `npm install && npm run build`
     - Start Command: `npm run preview`
   - Add environment variables:
     - `REACT_APP_API_URL` = your-backend-url.onrender.com

## Option 3: Deploy to Railway.app

### 1. Install Railway CLI
```bash
npm i -g @railway/cli
railway login
```

### 2. Backend Deployment
```bash
cd backend
railway init
railway add
# Select Python
railway variables add GROQ_API_KEY your_key_here
railway up
```

### 3. Frontend Deployment
```bash
cd ../frontend
railway init
railway add
# Select Node.js
railway up
```

## Option 4: Deploy to Heroku

### Backend (Deprecated, but still works)

```bash
# Install Heroku CLI
# https://devcenter.heroku.com/articles/heroku-cli

# Login
heroku login

# Create app
heroku create ai-architect-backend

# Add Procfile to backend folder
echo "web: gunicorn -w 4 main:app" > backend/Procfile

# Set environment variable
heroku config:set GROQ_API_KEY=your_key_here

# Deploy
git push heroku main

# View logs
heroku logs --tail
```

## Option 5: AWS Deployment

### Using Elastic Beanstalk

```bash
# Install EB CLI
pip install awsebcli

# Initialize
eb init -p python-3.11 ai-architect-backend

# Create environment
eb create ai-architect-env

# Set environment variables
eb setenv GROQ_API_KEY=your_key_here

# Deploy
eb deploy

# Open application
eb open
```

### Using ECS + Fargate

1. Push images to ECR
2. Create ECS cluster
3. Create task definitions
4. Create services for frontend and backend
5. Configure load balancer

## Production Configuration

### Backend (.env for production)

```
GROQ_API_KEY=production_key_here
BACKEND_PORT=8000
FRONTEND_URL=https://yourdomain.com
DEBUG=False
```

### Frontend (Update API URL)

Update `src/services/api.js`:
```javascript
const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://api.yourdomain.com';
```

## Performance Optimization

### Backend

1. **Use Production ASGI Server**
```bash
pip install gunicorn uvicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

2. **Enable Caching**
```python
from fastapi_cache2 import FastAPICache2

# Configure caching for stable responses
@cache(expire=3600)  # 1 hour cache
@app.get("/api/examples")
async def get_examples():
    ...
```

3. **Rate Limiting**
```python
from slowapi import Limiter

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/generate")
@limiter.limit("10/minute")
async def generate_blueprint():
    ...
```

### Frontend

1. **Build Optimization**
```bash
npm run build  # Creates optimized dist folder
```

2. **Serve from CDN**
   - Netlify, Vercel, AWS CloudFront
   - Enables global distribution

3. **Enable Gzip Compression**
   - Configure on reverse proxy (nginx, CloudFlare)
   - Reduces transfer size by 70%

## Monitoring & Logging

### Backend Logs

```python
import logging
from pythonjsonlogger import jsonlogger

logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter()
logHandler.setFormatter(formatter)

logger = logging.getLogger()
logger.addHandler(logHandler)
logger.setLevel(logging.INFO)
```

### Frontend Error Tracking

```javascript
// Add Sentry error tracking
import * as Sentry from "@sentry/react";

Sentry.init({
  dsn: "https://your-sentry-dsn@sentry.io/project-id",
  environment: "production",
});
```

### Metrics & Monitoring

- **Uptime Monitoring**: UptimeRobot, Pingdom
- **Error Tracking**: Sentry, Rollbar
- **Analytics**: Google Analytics for frontend
- **Application Monitoring**: New Relic, DataDog

## Security Checklist

- [ ] HTTPS/TLS enabled
- [ ] CORS properly configured
- [ ] API key not in version control
- [ ] Input validation on all endpoints
- [ ] Rate limiting enabled
- [ ] GZIP compression enabled
- [ ] Security headers set (HSTS, CSP, X-Frame-Options)
- [ ] Regular backups enabled
- [ ] Monitor for suspicious activity
- [ ] Keep dependencies updated

## Scaling Considerations

### For 1,000+ Concurrent Users

1. **Load Balancing**
   - Multiple backend instances
   - Load balancer (nginx, HAProxy)
   - Session management

2. **Caching Layer**
   - Redis for caching
   - CDN for frontend assets

3. **Database** (if added)
   - Read replicas
   - Connection pooling
   - Query optimization

4. **API Rate Limiting**
   - Per-user limits
   - Tier-based quotas

### Cost Estimation

- **Groq API**: $0.27-0.54 per 1M tokens
- **Hosting** (Render): $12-100+/month
- **CDN**: $0.12-0.85 per GB
- **Database** (if needed): $15-50+/month

## Troubleshooting Deployment

### Common Issues

| Issue | Solution |
|-------|----------|
| CORS errors | Update FRONTEND_URL in backend config |
| Blank frontend | Check API_URL environment variable |
| API timeout | Increase request timeout, check Groq quota |
| Memory issues | Increase container/instance memory |
| High latency | Enable caching, move closer region |

### Debugging Logs

```bash
# View backend logs
docker logs ai-architect-backend

# View frontend logs
docker logs ai-architect-frontend

# Using Railway
railway logs

# Using Render
# Via dashboard or CLI
```

## Continuous Deployment

### GitHub Actions Example

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Deploy Backend
        run: |
          # Deploy backend command
      - name: Deploy Frontend
        run: |
          # Deploy frontend command
```

## Rollback Strategy

1. Keep previous versions tagged
2. Test deployments in staging first
3. Enable quick rollback on production
4. Monitor metrics post-deployment

## Maintenance

- **Weekly**: Check logs for errors, review metrics
- **Monthly**: Update dependencies, test backups
- **Quarterly**: Review security, optimize queries
- **Annually**: Plan scaling, audit costs

---

**Version**: 1.0.0  
**Last Updated**: March 2026
