"""Database models for the RHD e-Registry."""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone

from .roles import CATEGORIES, LEVELS, PRIORITIES, ROLES, STATUSES

LEVEL_CHOICES = list(LEVELS.items())
ROLE_CHOICES = [(k, v['label']) for k, v in ROLES.items()]
STATUS_CHOICES = [(s, s) for s in STATUSES]
CATEGORY_CHOICES = [(c, c) for c in CATEGORIES]
PRIORITY_CHOICES = [(p, p) for p in PRIORITIES]


class Facility(models.Model):
    """The Regional Health Directorate, a District Health Directorate or a District Hospital."""
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20, unique=True, help_text='Used in reference numbers, e.g. RHD/26/0001')
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'facilities'
        ordering = ['id']

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra):
        if not username:
            raise ValueError('A username is required')
        user = self.model(username=username.strip().lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra):
        extra.setdefault('is_admin', True)
        extra.setdefault('role', 'SYSADMIN')
        if 'facility' not in extra and 'facility_id' not in extra:
            extra['facility'] = Facility.objects.filter(level='REGION').order_by('id').first() or \
                Facility.objects.create(name='Regional Health Directorate', code='RHD', level='REGION')
        extra.setdefault('name', 'System Administrator')
        return self.create_user(username, password, **extra)

    def get_by_natural_key(self, username):
        return self.get(username__iexact=username)


class User(AbstractBaseUser):
    """An officer. Signs in with a username; works at one facility in one role."""
    name = models.CharField(max_length=150)
    username = models.CharField(max_length=60, unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='users')
    phone = models.CharField(max_length=40, null=True, blank=True)
    email = models.CharField(max_length=190, null=True, blank=True)
    is_admin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.name} ({self.username})'

    # Django admin access for system administrators.
    @property
    def is_staff(self):
        return self.is_admin

    @property
    def is_superuser(self):
        return self.is_admin

    def has_perm(self, perm, obj=None):
        return self.is_active and self.is_admin

    def has_module_perms(self, app_label):
        return self.is_active and self.is_admin


class Letter(models.Model):
    ref_no = models.CharField(max_length=60, unique=True, null=True, blank=True)
    subject = models.CharField(max_length=300)
    summary = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='Letter')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='Normal')
    origin_facility = models.ForeignKey(Facility, null=True, blank=True, on_delete=models.PROTECT, related_name='letters_sent')
    external_sender = models.CharField(max_length=255, null=True, blank=True)
    external_ref = models.CharField(max_length=120, null=True, blank=True)
    letter_date = models.DateField(null=True, blank=True)
    dest_facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name='letters_received')
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='letters_created')
    holder = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name='letters_held')
    desk_since = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Drafted')
    due_date = models.DateField(null=True, blank=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='replies')
    created_at = models.DateTimeField(default=timezone.now)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-updated_at']
        indexes = [models.Index(fields=['status']), models.Index(fields=['updated_at']), models.Index(fields=['desk_since'])]

    def __str__(self):
        return f'{self.ref_no or "(not logged)"} {self.subject}'


class AuditTrailLocked(Exception):
    pass


class LetterEvent(models.Model):
    """One step in a letter's audit trail. Append-only: entries cannot be changed or deleted."""
    letter = models.ForeignKey(Letter, on_delete=models.PROTECT, related_name='events')
    at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name='events_done')
    action = models.CharField(max_length=40)
    to_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name='events_received')
    note = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20)

    class Meta:
        ordering = ['id']

    def save(self, *args, **kwargs):
        if self.pk is not None and not kwargs.get('force_insert'):
            raise AuditTrailLocked('The audit trail is append-only')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditTrailLocked('The audit trail is append-only')


class Attachment(models.Model):
    letter = models.ForeignKey(Letter, on_delete=models.PROTECT, related_name='attachments')
    filename = models.CharField(max_length=200)
    stored_name = models.CharField(max_length=80)
    mime = models.CharField(max_length=120)
    size = models.IntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['id']


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    letter = models.ForeignKey(Letter, null=True, blank=True, on_delete=models.SET_NULL)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-id']
        indexes = [models.Index(fields=['user', 'is_read'])]


class Outbox(models.Model):
    """SMS / email messages queued for delivery (see sms.py)."""
    channel = models.CharField(max_length=10)
    recipient = models.CharField(max_length=190)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, default='pending')   # pending | sending | retry | sent | failed
    attempts = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    provider = models.CharField(max_length=20, null=True, blank=True)
    provider_ref = models.CharField(max_length=100, null=True, blank=True)
    delivery_status = models.CharField(max_length=30, null=True, blank=True)
    delivery_checked_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    letter = models.ForeignKey(Letter, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name_plural = 'outbox'
        ordering = ['-id']
        indexes = [models.Index(fields=['channel', 'status'])]


class RefCounter(models.Model):
    prefix = models.CharField(max_length=40, primary_key=True)
    seq = models.IntegerField(default=0)


class Setting(models.Model):
    key = models.CharField(max_length=60, primary_key=True)
    value = models.TextField(blank=True, default='')

    def __str__(self):
        return self.key
