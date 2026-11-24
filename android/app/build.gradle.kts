plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val historyAssetsDirectory = layout.buildDirectory.dir("generated/historyAssets")
val historyAssets = tasks.register<Sync>("prepareHistoryAssets") {
    from(rootProject.file("../backend/session_history.py"), rootProject.file("../backend/tool_bridge.py"))
    into(historyAssetsDirectory)
}
tasks.named("preBuild") { dependsOn(historyAssets) }

android {
    namespace = "com.cassette.workbench"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.cassette.workbench"
        minSdk = 26
        targetSdk = 34
        versionCode = 53
        versionName = "0.1.51"
        testInstrumentationRunner = "com.cassette.workbench.TerminalInputInstrumentation"
    }
    // CI must explicitly use the restored key instead of the runner's default debug key.
    System.getenv("WORKBENCH_ANDROID_PREVIEW_KEYSTORE_PATH")?.takeIf { it.isNotBlank() }?.let { path ->
        signingConfigs.getByName("debug") {
            storeFile = file(path)
            storePassword = "android"
            keyAlias = "AndroidDebugKey"
            keyPassword = "android"
        }
    }
    buildFeatures { compose = true; buildConfig = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    testOptions { unitTests.isReturnDefaultValues = true }
    sourceSets.getByName("main").assets.srcDir(historyAssetsDirectory)
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
}
