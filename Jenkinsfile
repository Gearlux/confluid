pipeline {
    agent any

    environment {
        // Local virtual environment within the Jenkins workspace for portability
        VENV_PATH = "${WORKSPACE}/.venv"
        VENV_BIN = "${VENV_PATH}/bin"
    }

    stages {
        stage('Initialize') {
            steps {
                echo 'Creating Isolated Virtual Environment...'
                sh "python3 -m venv ${VENV_PATH}"
                
                echo 'Installing Dependencies in Editable Mode...'
                sh "${VENV_BIN}/pip install --upgrade pip"
                sh "${VENV_BIN}/pip install -e .[dev]"
            }
        }

        stage('Linting') {
            parallel {
                stage('Black') {
                    steps {
                        sh "${VENV_BIN}/black --check confluid tests examples"
                    }
                }
                stage('Isort') {
                    steps {
                        sh "${VENV_BIN}/isort --check-only confluid tests examples"
                    }
                }
                stage('Flake8') {
                    steps {
                        // Clean up previous reports
                        sh "rm -f flake8.txt flake8-report.xml"
                        // Generate flake8 output in a format that can be converted to JUnit XML
                        sh "${VENV_BIN}/flake8 confluid tests examples --tee --output-file=flake8.txt || true"
                        // Convert report to JUnit XML
                        sh "if [ -f flake8.txt ]; then ${VENV_BIN}/flake8_junit flake8.txt flake8-report.xml; fi"
                    }
                    post {
                        always {
                            junit allowEmptyResults: true, testResults: 'flake8-report.xml'
                        }
                    }
                }
            }
        }

        stage('Type Check') {
            steps {
                sh "${VENV_BIN}/mypy confluid tests examples"
            }
        }

        stage('Unit Tests') {
            steps {
                sh "${VENV_BIN}/pytest tests --junitxml=test-report.xml --cov=confluid --cov-report=xml:coverage.xml --cov-report=term"
            }
            post {
                always {
                    // Archive and display JUnit test results
                    junit allowEmptyResults: true, testResults: 'test-report.xml'
                    
                    // Display Coverage in Jenkins UI using Code Coverage API Plugin
                    recordCoverage tools: [[parser: 'COBERTURA', pattern: 'coverage.xml']]
                }
            }
        }

        stage('Verify Examples') {
            steps {
                echo 'Running project examples...'
                // Use single quotes for the shell command to prevent Groovy from trying to resolve $f
                sh '''
                    for f in examples/*.py; do
                        echo "Running $f..."
                        ${VENV_BIN}/python3 "$f"
                    done
                '''
            }
        }
    }

    post {
        always {
            echo 'Confluid Pipeline Complete.'
        }
        success {
            echo 'Confluid is healthy and ready for publication.'
        }
        failure {
            echo 'Confluid build failed. Please check linting or test failures.'
        }
    }
}
