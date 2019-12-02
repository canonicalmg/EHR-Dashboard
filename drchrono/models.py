from django.db import models
from django.db.models import Case, When
from endpoints import PatientEndpoint
from datetime import datetime
import logging

logger = logging.getLogger('')

class Patient(models.Model):
    id = models.IntegerField(primary_key=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    patient_photo = models.CharField(max_length=200)

    @property
    def full_name(self):
        return "{} {}".format(self.first_name, self.last_name)

    @staticmethod
    def create_from_api(pat):
        id = pat.get('id')
        first_name = pat.get('first_name')
        last_name = pat.get('last_name')
        patient_photo = pat.get('patient_photo')
        if not id and first_name and last_name:
            raise ValueError("Attempted and failed to create patient from API",
                             id, first_name, last_name)
        new_pat = Patient.objects.create(id=id,
                                         first_name=first_name,
                                         last_name=last_name,
                                         patient_photo=patient_photo)
        new_pat.save()

        return new_pat


class Appointment(models.Model):
    CONFIRMED = 'Confirmed'
    ARRIVED = 'Arrived'
    CHECKED_IN = 'Checked In'
    IN_SESSION = 'In Session'
    COMPLETE = 'Complete'
    RESCHEDULED = 'Rescheduled'
    CANCELED = 'Canceled'
    SCHEDULED = ''
    STATUS = [
        (CHECKED_IN, ('Patient Checked Into Room')),
        (ARRIVED, ('Patient Arrived')),
        (SCHEDULED, ('Appointment Scheduled')),
        (CONFIRMED, ('Appointment Confirmed')),
        (IN_SESSION, ('Appointment In Session')),
        (COMPLETE, ('Appointment Complete')),
        (RESCHEDULED, ('Appointment Rescheduled')),
        (CANCELED, ('Appointment Canceled'))
    ]
    # Arrange status items in order of how they should be displayed on dashboard
    # Sorting by IntegerField works but would require a backfill to old data any time we need to add a new status
    ORDER_HIERARCHY = Case(*[When(status=val[0], then=pos) for pos, val in enumerate(STATUS)])

    id = models.IntegerField(primary_key=True)
    scheduled_time = models.DateTimeField()
    duration = models.IntegerField()  # minutes
    status = models.CharField(max_length=32,
                              choices=STATUS,
                              default=CONFIRMED)
    reason = models.CharField(max_length=200, default="")
    # TODO: use drchrono API to store name of exam room
    exam_room = models.CharField(max_length=32, default="1")  # Currently holding ID
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    patient = models.ForeignKey(Patient)

    waiting_start = models.DateTimeField(null=True, blank=True)
    waiting_end = models.DateTimeField(null=True, blank=True)

    def waiting_for(self):
        """
        return minutes waiting since being checked in
        """
        if not self.waiting_start:
            return None
        if not self.waiting_end:
            return (datetime.now() - self.waiting_start).total_seconds() / 60
        return (self.waiting_end - self.waiting_start).total_seconds() / 60

    @staticmethod
    def create_from_api(apt, token):
        id = apt.get('id')
        scheduled_time = apt.get('scheduled_time')
        duration = apt.get('duration')
        status = apt.get('status')
        reason = apt.get('reason')
        exam_room = apt.get('exam_room')

        try:
            patient = Patient.objects.get(id=apt.get('patient'))
        except Patient.DoesNotExist:
            if not apt.get('patient'):
                return
                # raise ValueError("Failed to create appointment from API. Missing patient ID")
            pe = PatientEndpoint(access_token=token)
            patient_api = pe.fetch(apt.get('patient'))
            patient = Patient.create_from_api(patient_api)

        if not (id and scheduled_time and duration and status and patient):
            return
            # raise ValueError("Attempted and failed to create appointment from API. Missing Data",
                             # id, scheduled_time, duration, status, patient)

        waiting_start = None
        if status == Appointment.CHECKED_IN:
            # Take note of initial wait time
            if apt.get('status_transitions'):
                # Find transition leading to current status
                for t in apt.get('status_transitions'):
                    if t['to_status'] == status:
                        waiting_start = t['datetime']
                        continue
        elif status == 'In Room':
            # Normalizing field for calculation purposes
            status = Appointment.IN_SESSION

        new_apt = Appointment.objects.create(id=id,
                                             scheduled_time=scheduled_time,
                                             duration=duration,
                                             status=status,
                                             reason=reason,
                                             exam_room=exam_room,
                                             patient=patient,
                                             waiting_start=waiting_start)
        new_apt.save()

        return new_apt


