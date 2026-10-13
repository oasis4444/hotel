from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

ALL = "*"

RECEPTION = {
    "board.view", "reservation.view", "reservation.create", "reservation.validate", "reservation.cancel",
    "stay.checkin", "stay.checkout", "stay.extend", "client.view", "client.edit",
    "billing.view", "billing.pay", "shift.manage", "expense.request", "expense.approve", "expense.pay",
    "remit.make", "cleaning.verify", "cleaning.view", "planning.view",
    "order.create", "order.deliver", "kitchen.menu_view",
}

ROLE_PERMS = {
    "owner": {ALL},
    "manager": {ALL},
    "receptionist": RECEPTION,
    "housekeeper": {"cleaning.view", "cleaning.work", "expense.request", "order.deliver"},
    "cook": {"expense.request", "kitchen.view", "kitchen.manage", "kitchen.menu_view", "kitchen.menu_manage"},
    "maintenance": {"expense.request"},
}


def has_perm(membership, perm):
    if membership is None:
        return False
    perms = ROLE_PERMS.get(membership.role, set())
    return ALL in perms or perm in perms


class PermProxy:
    """Utilisable en template : {% if hperm.reservation_create %} ..."""

    def __init__(self, membership):
        self._m = membership

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return has_perm(self._m, name.replace("_", ".", 1))


def perm_required(perm):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.hotel:
                return render(request, "no_hotel.html", status=403)
            if not has_perm(request.membership, perm):
                return render(request, "forbidden.html", status=403)
            return view(request, *args, **kwargs)

        return login_required(wrapped)

    return decorator
