from typing import Optional

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.fields import JSONField

from .email_utils import get_user_facing_url
from .fields import MarkdownField
from .models import (
    SCRATCH_ORG_TYPES,
    TASK_REVIEW_STATUS,
    Epic,
    Project,
    ScratchOrg,
    SiteProfile,
    Task,
)
from .sf_run_flow import is_org_good
from .validators import CaseInsensitiveUniqueTogetherValidator, GitHubUserValidator

User = get_user_model()


class FormattableDict:
    """
    Stupid hack to get a dict error message into a
    CaseInsensitiveUniqueTogetherValidator, so the error can be assigned
    to a particular key.
    """

    def __init__(self, key, msg):
        self.key = key
        self.msg = msg

    def format(self, *args, **kwargs):
        return {
            self.key: self.msg.format(*args, **kwargs),
        }


class HashidPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    def to_representation(self, value):
        if self.pk_field is not None:
            return self.pk_field.to_representation(value.pk)
        return str(value.pk)


class FullUserSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    sf_username = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "avatar_url",
            "is_staff",
            "valid_token_for",
            "org_name",
            "org_type",
            "is_devhub_enabled",
            "sf_username",
            "currently_fetching_repos",
            "devhub_username",
            "uses_global_devhub",
            "agreed_to_tos_at",
        )

    def get_sf_username(self, obj) -> dict:
        if obj.uses_global_devhub:
            return None
        return obj.sf_username


class MinimalUserSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "avatar_url")


class ProjectSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    description_rendered = MarkdownField(source="description", read_only=True)
    repo_url = serializers.SerializerMethodField()
    repo_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = (
            "id",
            "name",
            "repo_url",
            "repo_owner",
            "repo_name",
            "description",
            "description_rendered",
            "is_managed",
            "slug",
            "old_slugs",
            "branch_prefix",
            "github_users",
            "repo_image_url",
        )

    def get_repo_url(self, obj) -> Optional[str]:
        return f"https://github.com/{obj.repo_owner}/{obj.repo_name}"

    def get_repo_image_url(self, obj) -> Optional[str]:
        return obj.repo_image_url if obj.include_repo_image_url else ""


class EpicSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    description_rendered = MarkdownField(source="description", read_only=True)
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(), pk_field=serializers.CharField()
    )
    branch_url = serializers.SerializerMethodField()
    branch_diff_url = serializers.SerializerMethodField()
    pr_url = serializers.SerializerMethodField()

    class Meta:
        model = Epic
        fields = (
            "id",
            "name",
            "description",
            "description_rendered",
            "slug",
            "old_slugs",
            "project",
            "branch_url",
            "branch_diff_url",
            "branch_name",
            "has_unmerged_commits",
            "currently_creating_pr",
            "pr_url",
            "pr_is_open",
            "pr_is_merged",
            "status",
            "github_users",
            "available_task_org_config_names",
            "currently_fetching_org_config_names",
        )
        extra_kwargs = {
            "slug": {"read_only": True},
            "old_slugs": {"read_only": True},
            "branch_url": {"read_only": True},
            "branch_diff_url": {"read_only": True},
            "has_unmerged_commits": {"read_only": True},
            "currently_creating_pr": {"read_only": True},
            "pr_url": {"read_only": True},
            "pr_is_open": {"read_only": True},
            "pr_is_merged": {"read_only": True},
            "status": {"read_only": True},
        }
        validators = (
            CaseInsensitiveUniqueTogetherValidator(
                queryset=Epic.objects.all(),
                fields=("name", "project"),
                message=FormattableDict(
                    "name", _("An epic with this name already exists.")
                ),
            ),
            GitHubUserValidator(parent="project"),
        )

    def create(self, validated_data):
        instance = super().create(validated_data)
        instance.create_gh_branch(self.context["request"].user)
        instance.queue_available_task_org_config_names(self.context["request"].user)
        return instance

    def validate(self, data):
        branch_name = data.get("branch_name", "")
        project = data.get("project", None)
        branch_name_differs = branch_name != getattr(self.instance, "branch_name", "")
        branch_name_changed = branch_name and branch_name_differs
        if branch_name_changed:
            if "__" in branch_name:
                raise serializers.ValidationError(
                    {
                        "branch_name": _(
                            'Only feature branch names (without "__") are allowed.'
                        )
                    }
                )

            branch_name_is_project_default_branch = (
                project and branch_name == project.branch_name
            )
            if branch_name_is_project_default_branch:
                raise serializers.ValidationError(
                    {
                        "branch_name": _(
                            "Cannot create an epic from the project default branch."
                        )
                    }
                )

            already_used_branch_name = (
                Epic.objects.active()
                .exclude(pk=getattr(self.instance, "pk", None))
                .filter(branch_name=branch_name)
                .exists()
            )
            if already_used_branch_name:
                raise serializers.ValidationError(
                    {"branch_name": _("This branch name is already in use.")}
                )

        return data

    def get_branch_diff_url(self, obj) -> Optional[str]:
        project = obj.project
        repo_owner = project.repo_owner
        repo_name = project.repo_name
        project_branch = project.branch_name
        branch = obj.branch_name
        if repo_owner and repo_name and project_branch and branch:
            return (
                f"https://github.com/{repo_owner}/{repo_name}/compare/"
                f"{project_branch}...{branch}"
            )
        return None

    def get_branch_url(self, obj) -> Optional[str]:
        project = obj.project
        repo_owner = project.repo_owner
        repo_name = project.repo_name
        branch = obj.branch_name
        if repo_owner and repo_name and branch:
            return f"https://github.com/{repo_owner}/{repo_name}/tree/{branch}"
        return None

    def get_pr_url(self, obj) -> Optional[str]:
        project = obj.project
        repo_owner = project.repo_owner
        repo_name = project.repo_name
        pr_number = obj.pr_number
        if repo_owner and repo_name and pr_number:
            return f"https://github.com/{repo_owner}/{repo_name}/pull/{pr_number}"
        return None


class TaskSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    description_rendered = MarkdownField(source="description", read_only=True)
    epic = serializers.PrimaryKeyRelatedField(
        queryset=Epic.objects.all(), pk_field=serializers.CharField()
    )
    branch_url = serializers.SerializerMethodField()
    branch_diff_url = serializers.SerializerMethodField()
    pr_url = serializers.SerializerMethodField()

    should_alert_dev = serializers.BooleanField(write_only=True, required=False)
    should_alert_qa = serializers.BooleanField(write_only=True, required=False)

    class Meta:
        model = Task
        fields = (
            "id",
            "name",
            "description",
            "description_rendered",
            "epic",
            "slug",
            "old_slugs",
            "has_unmerged_commits",
            "currently_creating_pr",
            "branch_name",
            "branch_url",
            "commits",
            "origin_sha",
            "branch_diff_url",
            "pr_url",
            "review_submitted_at",
            "review_valid",
            "review_status",
            "review_sha",
            "status",
            "pr_is_open",
            "assigned_dev",
            "assigned_qa",
            "should_alert_dev",
            "should_alert_qa",
            "currently_submitting_review",
            "org_config_name",
        )
        extra_kwargs = {
            "slug": {"read_only": True},
            "old_slugs": {"read_only": True},
            "has_unmerged_commits": {"read_only": True},
            "currently_creating_pr": {"read_only": True},
            "branch_url": {"read_only": True},
            "commits": {"read_only": True},
            "origin_sha": {"read_only": True},
            "branch_diff_url": {"read_only": True},
            "pr_url": {"read_only": True},
            "review_submitted_at": {"read_only": True},
            "review_valid": {"read_only": True},
            "review_status": {"read_only": True},
            "review_sha": {"read_only": True},
            "status": {"read_only": True},
            "pr_is_open": {"read_only": True},
            "currently_submitting_review": {"read_only": True},
        }
        validators = (
            CaseInsensitiveUniqueTogetherValidator(
                queryset=Task.objects.all(),
                fields=("name", "epic"),
                message=FormattableDict(
                    "name", _("A task with this name already exists.")
                ),
            ),
        )

    def get_branch_url(self, obj) -> Optional[str]:
        project = obj.epic.project
        repo_owner = project.repo_owner
        repo_name = project.repo_name
        branch = obj.branch_name
        if repo_owner and repo_name and branch:
            return f"https://github.com/{repo_owner}/{repo_name}/tree/{branch}"
        return None

    def get_branch_diff_url(self, obj) -> Optional[str]:
        epic = obj.epic
        epic_branch = epic.branch_name
        project = epic.project
        repo_owner = project.repo_owner
        repo_name = project.repo_name
        branch = obj.branch_name
        if repo_owner and repo_name and epic_branch and branch:
            return (
                f"https://github.com/{repo_owner}/{repo_name}/compare/"
                f"{epic_branch}...{branch}"
            )
        return None

    def get_pr_url(self, obj) -> Optional[str]:
        project = obj.epic.project
        repo_owner = project.repo_owner
        repo_name = project.repo_name
        pr_number = obj.pr_number
        if repo_owner and repo_name and pr_number:
            return f"https://github.com/{repo_owner}/{repo_name}/pull/{pr_number}"
        return None

    def create(self, validated_data):
        validated_data.pop("should_alert_dev", None)
        validated_data.pop("should_alert_qa", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        user = getattr(self.context.get("request"), "user", None)
        originating_user_id = str(user.id) if user else None
        self._handle_reassign(
            "dev", instance, validated_data, user, originating_user_id
        )
        self._handle_reassign("qa", instance, validated_data, user, originating_user_id)
        validated_data.pop("should_alert_dev", None)
        validated_data.pop("should_alert_qa", None)
        return super().update(instance, validated_data)

    def _handle_reassign(
        self, type_, instance, validated_data, user, originating_user_id
    ):
        assigned_user_has_changed = (
            getattr(instance, f"assigned_{type_}")
            != validated_data[f"assigned_{type_}"]
        )
        has_assigned_user = bool(validated_data[f"assigned_{type_}"])
        org_type = {"dev": SCRATCH_ORG_TYPES.Dev, "qa": SCRATCH_ORG_TYPES.QA}[type_]
        if assigned_user_has_changed and has_assigned_user:
            if validated_data.get(f"should_alert_{type_}"):
                self.try_send_assignment_emails(instance, type_, validated_data, user)
            reassigned_org = False
            # We want to consider soft-deleted orgs, too:
            orgs = [
                *instance.scratchorg_set.active().filter(org_type=org_type),
                *instance.scratchorg_set.inactive().filter(org_type=org_type),
            ]
            for org in orgs:
                new_user = self._valid_reassign(
                    type_, org, validated_data[f"assigned_{type_}"]
                )
                valid_commit = org.latest_commit == (
                    instance.commits[0] if instance.commits else instance.origin_sha
                )
                org_still_exists = is_org_good(org)
                if (
                    org_still_exists
                    and new_user
                    and valid_commit
                    and not reassigned_org
                ):
                    org.queue_reassign(
                        new_user=new_user, originating_user_id=originating_user_id
                    )
                    reassigned_org = True
                elif org.deleted_at is None:
                    org.delete(
                        originating_user_id=originating_user_id, preserve_sf_org=True
                    )
        elif not has_assigned_user:
            for org in [*instance.scratchorg_set.active().filter(org_type=org_type)]:
                org.delete(
                    originating_user_id=originating_user_id, preserve_sf_org=True
                )

    def _valid_reassign(self, type_, org, new_assignee):
        new_user = self.get_matching_assigned_user(
            type_, {f"assigned_{type_}": new_assignee}
        )
        if new_user and org.owner_sf_username == new_user.sf_username:
            return new_user
        return None

    def try_send_assignment_emails(self, instance, type_, validated_data, user):
        assigned_user = self.get_matching_assigned_user(type_, validated_data)
        if assigned_user:
            task = instance
            epic = task.epic
            project = epic.project
            metecho_link = get_user_facing_url(
                path=["projects", project.slug, epic.slug, task.slug]
            )
            subject = _("Metecho Task Assigned to You")
            body = render_to_string(
                "user_assigned_to_task.txt",
                {
                    "role": "Tester" if type_ == "qa" else "Developer",
                    "task_name": task.name,
                    "epic_name": epic.name,
                    "project_name": project.name,
                    "assigned_user_name": assigned_user.username,
                    "user_name": user.username if user else None,
                    "metecho_link": metecho_link,
                },
            )
            assigned_user.notify(subject, body)

    def get_matching_assigned_user(self, type_, validated_data):
        assigned = validated_data.get(f"assigned_{type_}", {})
        id_ = assigned.get("id") if assigned else None
        sa = SocialAccount.objects.filter(provider="github", uid=id_).first()
        return getattr(sa, "user", None)  # Optional[User]


class CreatePrSerializer(serializers.Serializer):
    title = serializers.CharField()
    critical_changes = serializers.CharField(allow_blank=True)
    additional_changes = serializers.CharField(allow_blank=True)
    issues = serializers.CharField(allow_blank=True)
    notes = serializers.CharField(allow_blank=True)
    alert_assigned_qa = serializers.BooleanField()


class ReviewSerializer(serializers.Serializer):
    notes = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(choices=TASK_REVIEW_STATUS)
    delete_org = serializers.BooleanField()
    org = serializers.PrimaryKeyRelatedField(
        queryset=ScratchOrg.objects.all(),
        pk_field=serializers.CharField(),
        allow_null=True,
    )


class ScratchOrgSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    task = serializers.PrimaryKeyRelatedField(
        queryset=Task.objects.all(), pk_field=serializers.CharField()
    )
    owner = serializers.PrimaryKeyRelatedField(
        pk_field=serializers.CharField(),
        default=serializers.CurrentUserDefault(),
        read_only=True,
    )
    unsaved_changes = serializers.SerializerMethodField()
    has_unsaved_changes = serializers.SerializerMethodField()
    total_unsaved_changes = serializers.SerializerMethodField()
    ignored_changes = serializers.SerializerMethodField()
    has_ignored_changes = serializers.SerializerMethodField()
    total_ignored_changes = serializers.SerializerMethodField()
    ignored_changes_write = JSONField(
        write_only=True, source="ignored_changes", required=False
    )
    valid_target_directories = serializers.SerializerMethodField()

    class Meta:
        model = ScratchOrg
        fields = (
            "id",
            "task",
            "org_type",
            "owner",
            "last_modified_at",
            "expires_at",
            "latest_commit",
            "latest_commit_url",
            "latest_commit_at",
            "last_checked_unsaved_changes_at",
            "url",
            "unsaved_changes",
            "total_unsaved_changes",
            "has_unsaved_changes",
            "ignored_changes",
            "ignored_changes_write",
            "total_ignored_changes",
            "has_ignored_changes",
            "currently_refreshing_changes",
            "currently_capturing_changes",
            "currently_refreshing_org",
            "currently_reassigning_user",
            "is_created",
            "delete_queued_at",
            "owner_gh_username",
            "has_been_visited",
            "valid_target_directories",
        )
        extra_kwargs = {
            "last_modified_at": {"read_only": True},
            "expires_at": {"read_only": True},
            "latest_commit": {"read_only": True},
            "latest_commit_url": {"read_only": True},
            "latest_commit_at": {"read_only": True},
            "last_checked_unsaved_changes_at": {"read_only": True},
            "url": {"read_only": True},
            "currently_refreshing_changes": {"read_only": True},
            "currently_capturing_changes": {"read_only": True},
            "currently_refreshing_org": {"read_only": True},
            "currently_reassigning_user": {"read_only": True},
            "is_created": {"read_only": True},
            "delete_queued_at": {"read_only": True},
            "owner_gh_username": {"read_only": True},
            "has_been_visited": {"read_only": True},
        }

    def _X_changes(self, obj, kind):
        user = getattr(self.context.get("request"), "user", None)
        if obj.owner == user:
            return getattr(obj, f"{kind}_changes")
        return {}

    def _has_X_changes(self, obj, kind) -> bool:
        return bool(getattr(obj, f"{kind}_changes", {}))

    def _total_X_changes(self, obj, kind) -> int:
        return sum(len(change) for change in getattr(obj, f"{kind}_changes").values())

    def get_unsaved_changes(self, obj) -> dict:
        return self._X_changes(obj, "unsaved")

    def get_has_unsaved_changes(self, obj) -> bool:
        return self._has_X_changes(obj, "unsaved")

    def get_total_unsaved_changes(self, obj) -> int:
        return self._total_X_changes(obj, "unsaved")

    def get_ignored_changes(self, obj) -> dict:
        return self._X_changes(obj, "ignored")

    def get_has_ignored_changes(self, obj) -> bool:
        return self._has_X_changes(obj, "ignored")

    def get_total_ignored_changes(self, obj) -> int:
        return self._total_X_changes(obj, "ignored")

    def get_valid_target_directories(self, obj) -> dict:
        user = getattr(self.context.get("request"), "user", None)
        if obj.owner == user:
            return obj.valid_target_directories
        return {}

    def validate(self, data):
        if (
            not self.instance
            and ScratchOrg.objects.active()
            .filter(task=data["task"], org_type=data["org_type"])
            .exists()
        ):
            raise serializers.ValidationError(
                _("A ScratchOrg of this type already exists for this task.")
            )
        return data

    def save(self, **kwargs):
        kwargs["owner"] = kwargs.get("owner", self.context["request"].user)
        return super().save(**kwargs)


class CommitSerializer(serializers.Serializer):
    commit_message = serializers.CharField()
    # Expect this to be Dict<str, List<str>>
    changes = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField())
    )
    target_directory = serializers.CharField()


class SiteSerializer(serializers.ModelSerializer):
    clickthrough_agreement = MarkdownField(read_only=True)

    class Meta:
        model = SiteProfile
        fields = (
            "name",
            "clickthrough_agreement",
        )


class CanReassignSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=("assigned_qa", "assigned_dev"))
    gh_uid = serializers.CharField()
