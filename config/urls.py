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
    # Medien (Rechnungs-/Angebots-PDFs, ZUGFeRD, Mahnungen) auch bei DEBUG=0
    # ausliefern – SEDO läuft als lokales Einzelplatz-Tool, es gibt keinen
    # vorgelagerten Webserver, der media/ übernehmen könnte.
    re_path(r'^media/(?P<path>.*)$', media_serve, {'document_root': settings.MEDIA_ROOT}, name='protected_media'),
]
