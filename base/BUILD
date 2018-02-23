load("//tools/base/bazel:maven.bzl", "maven_pom")

exports_files(["version.properties"])

# TODO: Extend POM generation to support what's needed for publishing.
maven_pom(
    name = "base_version",
    properties = "version.properties",
    version_property = "baseVersion",
    visibility = ["//visibility:public"],
)

maven_pom(
    name = "build_version",
    properties = "version.properties",
    version_property = "buildVersion",
    visibility = ["//visibility:public"],
)
