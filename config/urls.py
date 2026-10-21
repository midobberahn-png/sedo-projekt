from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.views.static import serve as media_serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('sedo.urls')),
]

# The broad file route is needed only by the local desktop profile. In the
# hosted profile, business documents use their narrow authenticated download
# and preview views; arbitrary media files cannot be rendered on the app origin.
if not getattr(settings, 'SEDO_WEB_MODE', False):
    urlpatterns.append(re_path(r'^media/(?P<path>.*)$', media_serve,
                               {'document_root': settings.MEDIA_ROOT}, name='protected_media'))
