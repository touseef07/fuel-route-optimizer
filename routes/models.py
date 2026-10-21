from django.db import models


class FuelStation(models.Model):
    opis_id = models.PositiveIntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=4, help_text="Retail price, USD per gallon")
    # City-level coordinates (from the US Census Gazetteer); null if the city could not be located.
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["state", "city", "price"]
        constraints = [models.UniqueConstraint(fields=["opis_id", "name", "address"], name="uniq_station_row")]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
