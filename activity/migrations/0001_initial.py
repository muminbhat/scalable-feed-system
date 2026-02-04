from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Event",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("actor_id", models.BigIntegerField(db_index=True)),
                ("verb", models.CharField(db_index=True, max_length=64)),
                ("object_type", models.CharField(db_index=True, max_length=64)),
                ("object_id", models.CharField(db_index=True, max_length=128)),
                ("created_at", models.DateTimeField(db_index=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["-created_at", "-id"], name="event_created_id_desc_idx"),
                    models.Index(fields=["verb", "-created_at", "-id"], name="event_verb_created_id_idx"),
                    models.Index(fields=["object_id", "-created_at", "-id"], name="event_obj_created_id_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="FeedItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_id", models.BigIntegerField(db_index=True)),
                ("created_at", models.DateTimeField(db_index=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="feed_items",
                        to="activity.event",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user_id", "-created_at", "-id"], name="feed_user_created_id_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_id", models.BigIntegerField(db_index=True)),
                ("created_at", models.DateTimeField(db_index=True)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="activity.event",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user_id", "-created_at", "-id"], name="notif_user_created_id_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IdempotencyKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=255, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "event",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="activity.event",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["-created_at"], name="idem_created_desc_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="feeditem",
            constraint=models.UniqueConstraint(fields=("user_id", "event"), name="uniq_feed_user_event"),
        ),
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.UniqueConstraint(fields=("user_id", "event"), name="uniq_notif_user_event"),
        ),
    ]

