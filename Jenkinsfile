pipeline {
    agent any

    environment {
        // Local virtual environment within the Jenkins workspace for portability
        VENV_PATH = "${WORKSPACE}/.venv"
        VENV_BIN = "${VENV_PATH}/bin"
        FORCE_JAVASCRIPT_ACTIONS_TO_NODE24 = 'true'
    }

    stages {
        stage('Initialize') {
            steps {
                echo 'Creating Isolated Virtual Environment...'
                sh "python3 -m venv ${VENV_PATH}"

                echo 'Installing Dependencies...'
                sh "${VENV_BIN}/pip install --upgrade pip"
                // Install our specific logflow from GitHub to avoid public PyPI confusion
                sh "${VENV_BIN}/pip install git+https://github.com/Gearlux/logflow.git@main"
                sh "${VENV_BIN}/pip install -e .[dev]"
            }
        }

        stage('Quality Gates') {
            parallel {
                stage('Black') {
                    steps {
                        script {
                            def rc = sh(script: "${VENV_BIN}/black --check --diff confluid tests examples > black-output.txt 2>&1", returnStatus: true)
                            sh """${VENV_BIN}/python3 -c "
import xml.etree.ElementTree as ET
rc = ${rc}
root = ET.Element('testsuite', name='black', tests='1', failures=str(min(rc, 1)))
tc = ET.SubElement(root, 'testcase', classname='black', name='formatting-check')
if rc != 0:
    with open('black-output.txt') as f:
        ET.SubElement(tc, 'failure', message='Black formatting issues found').text = f.read()
ET.ElementTree(root).write('black-report.xml', xml_declaration=True, encoding='unicode')
" """
                        }
                    }
                    post {
                        always {
                            recordCoverage(
                                id: 'black-confluid',
                                name: 'Black Formatting (Confluid)',
                                tools: [[parser: 'JUNIT', pattern: 'black-report.xml']]
                            )
                        }
                    }
                }
                stage('Isort') {
                    steps {
                        script {
                            def rc = sh(script: "${VENV_BIN}/isort --check-only --diff confluid tests examples > isort-output.txt 2>&1", returnStatus: true)
                            sh """${VENV_BIN}/python3 -c "
import xml.etree.ElementTree as ET
rc = ${rc}
root = ET.Element('testsuite', name='isort', tests='1', failures=str(min(rc, 1)))
tc = ET.SubElement(root, 'testcase', classname='isort', name='import-order-check')
if rc != 0:
    with open('isort-output.txt') as f:
        ET.SubElement(tc, 'failure', message='Isort import order issues found').text = f.read()
ET.ElementTree(root).write('isort-report.xml', xml_declaration=True, encoding='unicode')
" """
                        }
                    }
                    post {
                        always {
                            recordCoverage(
                                id: 'isort-confluid',
                                name: 'Isort Import Order (Confluid)',
                                tools: [[parser: 'JUNIT', pattern: 'isort-report.xml']]
                            )
                        }
                    }
                }
                stage('Flake8') {
                    steps {
                        script {
                            def rc = sh(script: "${VENV_BIN}/flake8 confluid tests examples > flake8-output.txt 2>&1", returnStatus: true)
                            sh """${VENV_BIN}/python3 -c "
import xml.etree.ElementTree as ET
rc = ${rc}
root = ET.Element('testsuite', name='flake8', tests='1', failures=str(min(rc, 1)))
tc = ET.SubElement(root, 'testcase', classname='flake8', name='lint-check')
if rc != 0:
    with open('flake8-output.txt') as f:
        ET.SubElement(tc, 'failure', message='Flake8 linting issues found').text = f.read()
ET.ElementTree(root).write('flake8-report.xml', xml_declaration=True, encoding='unicode')
" """
                        }
                    }
                    post {
                        always {
                            recordCoverage(
                                id: 'flake8-confluid',
                                name: 'Flake8 (Confluid)',
                                tools: [[parser: 'JUNIT', pattern: 'flake8-report.xml']]
                            )
                        }
                    }
                }
                stage('Mypy') {
                    steps {
                        script {
                            sh "${VENV_BIN}/mypy confluid tests examples --junit-xml=mypy-report.xml || true"
                        }
                    }
                    post {
                        always {
                            recordCoverage(
                                id: 'mypy-confluid',
                                name: 'Mypy (Confluid)',
                                tools: [[parser: 'JUNIT', pattern: 'mypy-report.xml']]
                            )
                        }
                    }
                }
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
                    
                    // Display Coverage and Test Results as separate graphs using unique IDs
                    recordCoverage(
                        id: 'unit-tests-confluid',
                        name: 'Unit Tests (Confluid)',
                        tools: [[parser: 'JUNIT', pattern: 'test-report.xml']]
                    )
                    recordCoverage(
                        id: 'coverage-confluid',
                        name: 'Code Coverage (Confluid)',
                        tools: [[parser: 'COBERTURA', pattern: 'coverage.xml']]
                    )
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
