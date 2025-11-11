"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, re_path
from analytics.views import dashboard, edit_lineup

import httpx
from django.http import HttpResponse, StreamingHttpResponse

async def prefect_proxy(request, path=''):
    """Proxy requests to Prefect UI"""
    prefect_url = f"http://localhost:4200/{path}"
    
    async with httpx.AsyncClient() as client:
        # Forward the request
        response = await client.request(
            method=request.method,
            url=prefect_url,
            params=request.GET,
            headers={k: v for k, v in request.headers.items() 
                    if k.lower() not in ['host', 'connection']},
            content=request.body,
        )
        
        # Return the response
        django_response = HttpResponse(
            content=response.content,
            status=response.status_code,
        )
        
        # Copy headers
        for key, value in response.headers.items():
            if key.lower() not in ['content-encoding', 'transfer-encoding']:
                django_response[key] = value
                
        return django_response

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('lineup/edit/', edit_lineup, name='edit_lineup'),
    path('admin/', admin.site.urls),
    re_path(r'^prefect/(?P<path>.*)$', prefect_proxy, name='prefect_proxy'),
]
