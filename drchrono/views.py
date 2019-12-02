from django.shortcuts import redirect
from django.views.generic import TemplateView
from django.views import View
from social_django.models import UserSocialAuth
from datetime import datetime, timedelta
from django.http.response import HttpResponse
from drchrono.endpoints import DoctorEndpoint, AppointmentEndpoint
from models import Appointment, Patient
import logging

logger = logging.getLogger('')

class SetupView(TemplateView):
    """
    The beginning of the OAuth sign-in flow. Logs a user into the kiosk, and saves the token.
    """
    template_name = 'kiosk_setup.html'


class AppointmentStatusChange(View):
    """
    Change status of an appointment
    """
    def get(self, request):
        id = request.GET.get('id')
        status = request.GET.get('status')
        if not (id and status):
            return HttpResponse('Error. Valid Appointment ID and status required.', status=400)
        if status not in [Appointment.IN_SESSION, Appointment.COMPLETE, Appointment.RESCHEDULED]:
            return HttpResponse('Error. Status invalid.', status=400)

        apt = Appointment.objects.get(id=id)
        apt.status = status

        # Check if timers need to be set
        if status == Appointment.IN_SESSION:
            if not apt.waiting_start:
                apt.waiting_start = datetime.now()
            apt.waiting_end = datetime.now()

        access_token = self.get_token()
        api = AppointmentEndpoint(access_token)
        api.update(id, {'status': status})
        apt.save()

        return HttpResponse(id)

    def get_token(self):
        """
        Social Auth module is configured to store our access tokens. This dark magic will fetch it for us if we've
        already signed in.
        """
        oauth_provider = UserSocialAuth.objects.get(provider='drchrono')
        access_token = oauth_provider.extra_data['access_token']
        return access_token


class DoctorWelcome(TemplateView):
    """
    The doctor can see what appointments they have today.
    """
    template_name = 'doctor_welcome.html'
    today_date = datetime.today()

    def get_token(self):
        """
        Social Auth module is configured to store our access tokens. This dark magic will fetch it for us if we've
        already signed in.
        """
        oauth_provider = UserSocialAuth.objects.get(provider='drchrono')
        access_token = oauth_provider.extra_data['access_token']
        return access_token

    def get_doctor(self):
        """
        Use the token we have stored in the DB to make an API request and get doctor details. If this succeeds, we've
        proved that the OAuth setup is working
        """
        # We can create an instance of an endpoint resource class, and use it to fetch details
        access_token = self.get_token()
        api = DoctorEndpoint(access_token)
        # Grab the first doctor from the list; normally this would be the whole practice group, but your hackathon
        # account probably only has one doctor in it.
        return api.list()[0]

    def get_appointments(self, doctor):
        """
        Return all appointments for today

        TODO: Performing local db sync on page load slows performance, even if by a non-negligible amount
              Write cron/celery task to periodically sync so we can remove the API call from here
        """
        access_token = self.get_token()
        api = AppointmentEndpoint(access_token)
        apts_api = api.list(params={"verbose": True, "doctor": doctor['id']}, date=self.today_date)
        for apt in apts_api:
            try:
                #  TODO: Update status locally if value from API result differs
                apt_db = Appointment.objects.get(id=apt['id'])
                #  Check if status has changed and update if needed
                # if apt_db.status != apt['status']:
                #     apt_db.status = apt['status']
                #     apt_db.save()
            except Appointment.DoesNotExist:
                Appointment.create_from_api(apt, access_token)

        # Sort appointments by status, in order of time_spent_waiting
        yesterday = datetime.today() - timedelta(days=1)
        apts_db = Appointment.objects\
            .filter(scheduled_time__gt=yesterday)\
            .exclude(status=Appointment.IN_SESSION)\
            .order_by(Appointment.ORDER_HIERARCHY)
        return apts_db

    def get_current_or_next_appointment(self):
        returned_apt = {}

        # Only one appointment should ever be in session at a time.
        current_apt = Appointment.objects.filter(status=Appointment.IN_SESSION).first()
        if not current_apt:
            next_apt = Appointment.objects\
                .filter(status__in=[Appointment.CHECKED_IN,
                                    Appointment.ARRIVED,
                                    Appointment.CONFIRMED])\
                .order_by(Appointment.ORDER_HIERARCHY).first()
            returned_apt = {'type': 'Next', 'apt': next_apt}
        else:
            returned_apt = {'type': 'Current', 'apt': current_apt}

        return returned_apt

    def get_stats(self):
        stats = {}
        # Average Wait Time (with respect to average duration) in minutes
        wait_time = 0
        duration = 0
        count = 0
        for apt in Appointment.objects.filter(waiting_start__isnull=False, waiting_end__isnull=False):
            t_delta = apt.waiting_end - apt.waiting_start
            wait_time += (t_delta.total_seconds() / 60)
            duration += apt.duration
            count += 1
        stats['avg_wait_time'] = wait_time / count if count > 0 else 0
        stats['avg_duration'] = duration / count if count > 0 else 0

        # Appointments Serviced
        appointments_serviced = [0] * 7
        day_counter = datetime.now().weekday()
        while day_counter >= 0:
            appointments_serviced[day_counter] = Appointment.objects.filter(scheduled_time__date=datetime.now() - timedelta(days=day_counter)).count()
            day_counter -= 1
        stats['appointments_serviced'] = appointments_serviced
        return stats

    def get_context_data(self, **kwargs):
        kwargs = super(DoctorWelcome, self).get_context_data(**kwargs)
        # Hit the API using one of the endpoints just to prove that we can
        # If this works, then your oAuth setup is working correctly.
        doctor_details = self.get_doctor()
        kwargs['appointments'] = self.get_appointments(doctor_details)
        kwargs['current_or_next_apt'] = self.get_current_or_next_appointment()
        kwargs['doctor'] = doctor_details
        kwargs['today'] = self.today_date
        kwargs['stats'] = self.get_stats()
        return kwargs

